"""Fixed offline CLI for observation-driven decisions and stopping."""

import json
import sys

from engine import run_loop
from policies import decide_evidence_first, decide_repeat_search
from tools import AssetsError, LocalTools, assets_sha256, load_assets

CONTRACT_VERSION = "agent-loop-v1"
HELP = """观察驱动的决策循环实验（无模型、无网络）
用法：python -m agent_loop --case found|empty --policy evidence_first|repeat_search --max-steps 1|2|3|4|5
每次决策（包括 finish）占一轮；退出 0 也可能是 no_evidence 或 step_limit。
"""


def _arguments(argv: list[str]) -> tuple[str, str, int] | None:
    if len(argv) != 6:
        return None
    values = {}
    for index in range(0, 6, 2):
        flag, value = argv[index : index + 2]
        if flag not in {"--case", "--policy", "--max-steps"} or flag in values:
            return None
        values[flag] = value
    if (
        values["--case"] not in {"found", "empty"}
        or values["--policy"] not in {"evidence_first", "repeat_search"}
        or values["--max-steps"] not in {"1", "2", "3", "4", "5"}
    ):
        return None
    return values["--case"], values["--policy"], int(values["--max-steps"])


def _emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def _error(code: str, message: str, exit_code: int) -> int:
    _emit(
        {
            "contract_version": CONTRACT_VERSION,
            "ok": False,
            "error": {"code": code, "message": message},
        }
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--help"]:
        print(HELP, end="")
        return 0
    parsed = _arguments(arguments)
    if parsed is None:
        return _error("invalid_input", "循环实验参数无效", 2)
    case_id, policy, max_steps = parsed
    try:
        assets = load_assets()
    except AssetsError:
        return _error("assets_invalid", "循环实验资料无效", 1)
    try:
        query = next(case["query"] for case in assets["cases"] if case["id"] == case_id)
        tools = LocalTools(assets)
        decide = decide_evidence_first if policy == "evidence_first" else decide_repeat_search
        result = run_loop(query, decide, max_steps, search=tools.search, read=tools.read)
        report = {
            "contract_version": CONTRACT_VERSION,
            "ok": True,
            "lesson_id": "agent-agent-loop",
            "asset_version": assets["asset_version"],
            "assets_sha256": assets_sha256(assets),
            "case_id": case_id,
            "policy": policy,
            "decision_source": "deterministic_policy",
            "read_only": True,
            "model_calls": 0,
            **result,
        }
    except Exception:
        return _error("execution_failed", "循环实验未产生完整报告", 1)
    _emit(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
