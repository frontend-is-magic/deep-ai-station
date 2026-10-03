"""Verify fixed SQLite lab ZIPs through independent real processes and database faults."""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path

from build_course_labs import DESTINATION, ROOT, STORAGE_FILES
from verify_course_labs import command


def verify_cases(server, folder, env):
    suite = json.loads((folder / "contract-cases.json").read_text())
    if suite["version"] != "sqlite-storage-v1":
        raise RuntimeError("Unexpected SQLite contract version")
    count = 0
    with tempfile.TemporaryDirectory(prefix="deep-ai-sqlite-data-") as temporary:
        for case in suite["cases"]:
            database = Path(temporary) / f"{case['id']}.sqlite"
            for index, step in enumerate(case["steps"]):
                if "sql" in step or "assert_sql" in step:
                    connection = sqlite3.connect(database)
                    try:
                        if "sql" in step:
                            connection.executescript(step["sql"])
                        else:
                            actual = [list(row) for row in connection.execute(step["assert_sql"])]
                            if actual != step["rows"]:
                                raise RuntimeError(f"{case['id']}/{index}: SQL assertion failed")
                    finally:
                        connection.close()
                    continue
                if "raw_hex" in step:
                    data = bytes.fromhex(step["raw_hex"])
                elif "raw" in step:
                    data = step["raw"].encode()
                else:
                    data = json.dumps(step["request"], ensure_ascii=False).encode()
                result = subprocess.run(  # noqa: S603 - fixed maintainer CLI, no shell or provider secrets
                    [*server, str(database)],
                    input=data,
                    cwd=folder,
                    env=env,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                if result.returncode != step["exit_code"]:
                    raise RuntimeError(f"{case['id']}/{index}: unexpected exit {result.returncode}")
                try:
                    value = json.loads(result.stdout.decode("utf-8"))
                except (ValueError, UnicodeError) as error:
                    raise RuntimeError(f"{case['id']}/{index}: invalid stdout JSON") from error
                # JSON booleans must not compare equal to Python integers (True == 1).
                actual_json = json.dumps(value, sort_keys=True, allow_nan=False)
                expected_json = json.dumps(step["expected"], sort_keys=True, allow_nan=False)
                if actual_json != expected_json:
                    raise RuntimeError(f"{case['id']}/{index}: response differs from contract")
                count += 1
    return len(suite["cases"]), count


def verify(language):
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "USER",
        "LOGNAME",
        "CI",
        "UV_CACHE_DIR",
        "GOCACHE",
        "GOMODCACHE",
        "GOPATH",
        "GOTOOLCHAIN",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "PNPM_HOME",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    with tempfile.TemporaryDirectory(prefix=f"deep-ai-sqlite-{language}-") as temporary:
        folder = Path(temporary).resolve()
        if folder.is_relative_to(ROOT):
            raise RuntimeError("Archive checks must run outside the repository")
        with zipfile.ZipFile(DESTINATION / f"sqlite-storage-{language}.zip") as archive:
            archive.extractall(folder)
        if language == "python":
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError("uv is required")
            command([uv, "sync", "--locked"], folder, env)
            command([uv, "run", "--frozen", "ruff", "check", "."], folder, env)
            command([uv, "run", "--frozen", "ruff", "format", "--check", "."], folder, env)
            command([uv, "run", "--frozen", "pytest", "-q"], folder, env)
            server = [str(folder / ".venv/bin/python"), "app.py"]
        elif language == "typescript":
            command(["pnpm", "install", "--frozen-lockfile"], folder, env)
            config = subprocess.check_output(  # noqa: S603 - fixed maintainer formatter
                [shutil.which("pnpm"), "exec", "prettier", "--find-config-path", "src/main.ts"],
                cwd=folder,
                env=env,
                text=True,
            ).strip()
            if (folder / config).resolve() != folder / ".prettierrc.json":
                raise RuntimeError("Lab did not resolve its own Prettier config")
            command(["pnpm", "check"], folder, env)
            server = ["node", "dist/main.js"]
        else:
            files = sorted(path.name for path in folder.glob("*.go"))
            unformatted = subprocess.check_output(  # noqa: S603 - fixed maintainer sources
                [shutil.which("gofmt"), "-l", *files],
                cwd=folder,
                env=env,
                text=True,
            )
            if unformatted.strip():
                raise RuntimeError("Go lab is not formatted")
            command(["go", "test", "-mod=readonly", "./..."], folder, env)
            command(["go", "build", "-mod=readonly", "-o", "storage-lab", "."], folder, env)
            server = [str(folder / "storage-lab")]
        scenarios, count = verify_cases(server, folder, env)
        print(
            json.dumps(
                {
                    "language": language,
                    "archive": "verified",
                    "scenarios": scenarios,
                    "cli_processes": count,
                    "model_calls": 0,
                }
            )
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=STORAGE_FILES)
    args = parser.parse_args()
    for language in [args.language] if args.language else STORAGE_FILES:
        verify(language)


if __name__ == "__main__":
    main()
