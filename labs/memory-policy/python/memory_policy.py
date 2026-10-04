"""Fixed-path, read-only CLI. It never interprets text or starts another program."""

import os
import sys

from loader import CONTRACT_VERSION, LANGUAGES, LabError, canonical, load_assets
from policy import evaluate, list_cases, validate_report


def parse_args(args):
    if args == ["--list"]:
        return "list", None, None
    if len(args) not in (2, 4):
        raise LabError("invalid_input")
    options = {}
    for index in range(0, len(args), 2):
        key, value = args[index : index + 2]
        if (
            key not in ("--case", "--language")
            or key in options
            or not value
            or value.startswith("--")
        ):
            raise LabError("invalid_input")
        options[key] = value
    if "--case" not in options or (
        "--language" in options and options["--language"] not in LANGUAGES
    ):
        raise LabError("invalid_input")
    return "resolve", options["--case"], options.get("--language")


def run(args):
    command, case_id, language = parse_args(args)
    memories, cases = load_assets()
    report = (
        list_cases(memories, cases)
        if command == "list"
        else evaluate(memories, cases, case_id, language)
    )
    validate_report(report, memories, cases)
    return report


def emit(report):
    data = canonical(report).encode("utf-8") + b"\n"
    try:
        while data:
            written = os.write(1, data)
            if written <= 0:
                return False
            data = data[written:]
        return True
    except OSError:
        return False


def main(args=None):
    try:
        report = run(sys.argv[1:] if args is None else args)
        code = 0
    except LabError as error:
        report = {"contract_version": CONTRACT_VERSION, "error": error.code}
        code = 1
    except Exception:
        report = {"contract_version": CONTRACT_VERSION, "error": "experiment_failed"}
        code = 1
    return code if emit(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
