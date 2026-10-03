"""All user code runs in a disposable remote sandbox, never on the API host."""

import asyncio
import os
import time
from uuid import uuid4

from fastapi import HTTPException

OUTPUT_LIMIT = 20_000
_slots = asyncio.Semaphore(2)


def sandbox_capabilities() -> dict:
    enabled = bool(os.getenv("E2B_API_KEY") and os.getenv("PLAYGROUND_ACCESS_TOKEN"))
    return {
        "enabled": enabled,
        "languages": (["python", "typescript"] + (["go"] if os.getenv("E2B_GO_TEMPLATE") else []))
        if enabled
        else [],
        "timeout_seconds": 12,
        "network": "denied",
    }


class OutputLimit(Exception):
    pass


class Output:
    def __init__(self):
        self.stdout = ""
        self.stderr = ""
        self.truncated = False

    def append(self, channel: str, message):
        text = message if isinstance(message, str) else message.line
        remaining = OUTPUT_LIMIT - len(self.stdout) - len(self.stderr)
        setattr(self, channel, getattr(self, channel) + text[:remaining])
        if len(text) > remaining:
            self.truncated = True
            raise OutputLimit


async def execute_code(language: str, source: str, factory=None) -> dict:
    if language not in sandbox_capabilities()["languages"]:
        raise HTTPException(503, "该语言的隔离运行尚未配置")
    if _slots.locked():
        raise HTTPException(429, "沙箱忙碌，请稍后重试")
    from e2b import CommandExitException
    from e2b.exceptions import TimeoutException
    from e2b_code_interpreter import AsyncSandbox

    factory = factory or AsyncSandbox
    started = time.monotonic()
    run_id = str(uuid4())
    sandbox = None
    output = Output()
    passed = False
    status = "failed"
    cleanup = "not-started"
    async with _slots:
        try:
            async with asyncio.timeout(25):
                sandbox = await factory.create(
                    template=os.getenv("E2B_GO_TEMPLATE") if language == "go" else None,
                    timeout=45,
                    secure=True,
                    allow_internet_access=False,
                    envs={},
                    metadata={"project": "deep-ai-station", "language": language, "run_id": run_id},
                    api_key=os.getenv("E2B_API_KEY"),
                    request_timeout=8,
                    retries=0,
                    debug=False,
                )
                if language == "go":
                    await sandbox.files.write("/home/user/main.go", source, request_timeout=5)
                    result = await sandbox.commands.run(
                        "GOTOOLCHAIN=local GOCACHE=/tmp/go-cache go run /home/user/main.go",
                        timeout=12,
                        request_timeout=15,
                        on_stdout=lambda text: output.append("stdout", text),
                        on_stderr=lambda text: output.append("stderr", text),
                    )
                    passed = result.exit_code == 0
                else:
                    result = await sandbox.run_code(
                        source,
                        language=language,
                        timeout=12,
                        request_timeout=15,
                        envs={},
                        on_stdout=lambda message: output.append("stdout", message),
                        on_stderr=lambda message: output.append("stderr", message),
                    )
                    passed = result.error is None
                    for item in result.results:
                        if item.text:
                            output.append("stdout", item.text)
                    if result.error:
                        output.append("stderr", f"{result.error.name}: {result.error.value}")
                status = "completed" if passed else "failed"
        except OutputLimit:
            status, passed = "output-limit", False
        except (TimeoutError, TimeoutException):
            status, passed = "timeout", False
        except CommandExitException:
            status, passed = "failed", False
        except Exception as exc:
            # SDK errors may contain credentials or service response bodies.
            raise HTTPException(503, "沙箱服务不可用，请检查托管配置") from exc
        finally:
            if sandbox:
                try:
                    destroyed = await asyncio.shield(sandbox.kill(request_timeout=5, retries=0))
                    cleanup = "destroyed" if destroyed else "not-found"
                except Exception:
                    # The creation TTL remains 45 seconds even if cleanup networking fails.
                    cleanup = "expiry-fallback"
    return {
        "mode": "isolated-sandbox",
        "executed": sandbox is not None,
        "run_id": run_id,
        "language": language,
        "passed": passed,
        "status": status,
        "stdout": output.stdout,
        "stderr": output.stderr,
        "truncated": output.truncated,
        "cleanup": cleanup,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "notice": "代码仅提交至独立 E2B 沙箱；禁止出站网络，12 秒运行上限，45 秒实例存活上限。框架依赖需预先安装。",
    }
