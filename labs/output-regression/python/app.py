"""Fixed local CLI: whole-asset validation precedes every comparison or explanation."""

import os
import sys

from evaluator import compare, explain
from loader import CASE_IDS, EXPERIMENT_VERSION, PROFILE_IDS, LabError, canonical, load_assets

COMMANDS = {"compare": {"candidate", "split"}, "explain": {"profile", "case"}}
HELP = """Usage: python app.py <command> [options]
  compare --candidate baseline|unsafe-candidate|fixed-candidate --split dev|acceptance
  explain --profile baseline|unsafe-candidate|fixed-candidate --case CASE_ID
Fixed public synthetic outputs only; no model calls or production release decision.
Dev and acceptance are separate public teaching splits, not secret holdouts.
Exit: compare 0=gate passed, 2=gate rejected; explain 0=explained; 1=input/assets error.
"""


def parse_arguments(argv: list[str]) -> tuple[str, dict]:
    if len(argv) != 5 or argv[0] not in COMMANDS:
        raise LabError("invalid_input")
    command, arguments = argv[0], {}
    for index in (1, 3):
        flag, value = argv[index], argv[index + 1]
        if not flag.startswith("--") or flag[2:] not in COMMANDS[command] or flag[2:] in arguments:
            raise LabError("invalid_input")
        arguments[flag[2:]] = value
    if command == "compare":
        valid = arguments["candidate"] in PROFILE_IDS and arguments["split"] in (
            "dev",
            "acceptance",
        )
    else:
        valid = arguments["profile"] in PROFILE_IDS and arguments["case"] in CASE_IDS
    if not valid:
        raise LabError("invalid_input")
    return command, arguments


def execute(argv: list[str]) -> tuple[dict, int]:
    command, arguments = parse_arguments(argv)
    assets = load_assets()
    if command == "compare":
        result = compare(assets, arguments["candidate"], arguments["split"])
        return result, 0 if result["gate"]["passed"] else 2
    return explain(assets, arguments["profile"], arguments["case"]), 0


def write_stdout(text: str) -> bool:
    # Avoid buffered interpreter-shutdown diagnostics after a closed downstream pipe.
    pending = text.encode("utf-8", "strict")
    try:
        while pending:
            written = os.write(1, pending)
            if written <= 0:
                return False
            pending = pending[written:]
        return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--help"] or (
        len(arguments) == 2 and arguments[0] in COMMANDS and arguments[1] == "--help"
    ):
        return 0 if write_stdout(HELP) else 1
    try:
        result, code = execute(arguments)
    except LabError as error:
        result, code = {"experiment_version": EXPERIMENT_VERSION, "error": {"code": error.code}}, 1
    except Exception:
        result, code = (
            {"experiment_version": EXPERIMENT_VERSION, "error": {"code": "experiment_failed"}},
            1,
        )
    return code if write_stdout(canonical(result) + "\n") else 1


if __name__ == "__main__":
    raise SystemExit(main())
