"""A bounded, fixed-workflow CLI. No user commands, model calls, or network access."""

import sys

from repository import execute
from workflow import VERSION, LabError, canonical


def parse_args(arguments):
    if not arguments or arguments[0] not in {"start", "inspect", "step", "resume"}:
        raise LabError("invalid_input")
    command, *rest = arguments
    required = {
        "start": {"run-id", "case"},
        "inspect": {"run-id"},
        "step": {"run-id", "expected-revision"},
        "resume": {"run-id", "expected-revision"},
    }[command]
    allowed = required | ({"fault", "fault-node"} if command in {"step", "resume"} else set())
    if len(rest) % 2:
        raise LabError("invalid_input")
    fields = {}
    for option, value in zip(rest[::2], rest[1::2], strict=True):
        if not option.startswith("--") or option[2:] not in allowed or option[2:] in fields:
            raise LabError("invalid_input")
        fields[option[2:]] = value
    if not required.issubset(fields):
        raise LabError("invalid_input")
    revision = fields.get("expected-revision")
    if revision is not None and revision not in {"0", "1", "2", "3"}:
        raise LabError("invalid_input")
    return (
        command,
        fields["run-id"],
        {
            "case_id": fields.get("case"),
            "expected_revision": int(revision) if revision is not None else None,
            "fault": fields.get("fault"),
            "fault_node": fields.get("fault-node"),
        },
    )


def main(arguments=None):
    exit_code = 0
    try:
        command, run_id, options = parse_args(sys.argv[1:] if arguments is None else arguments)
        result = execute(command, run_id, **options)
    except LabError as error:
        result = {
            "contract_version": VERSION,
            "ok": False,
            "error": {"code": error.code},
            "performed": error.performed,
        }
        exit_code = 1
    except Exception:
        result = {
            "contract_version": VERSION,
            "ok": False,
            "error": {"code": "storage_failure"},
            "performed": [],
        }
        exit_code = 1
    try:
        print(canonical(result), flush=True)
    except (OSError, UnicodeError):
        # A closed output pipe cannot receive an acknowledgement. Keep diagnostics
        # fixed and silent; the caller must inspect durable state in a new process.
        try:
            sys.stdout.close()
        except OSError:
            pass
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
