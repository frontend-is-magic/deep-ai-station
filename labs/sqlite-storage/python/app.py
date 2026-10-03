"""一个进程处理一条 JSON 命令；数据库路径只通过 argv 提供。"""

import json
import math
import re
import sys
from datetime import datetime

from repository import StorageError, execute

MAX_INPUT_BYTES = 4096
MAX_CURSOR = 9007199254740991
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
TIMESTAMP = re.compile(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z")
FIELDS = {
    "migrate": {"op"},
    "set_progress": {"op", "owner_id", "lesson_id", "completed", "at"},
    "list_progress": {"op", "owner_id", "cursor", "limit"},
    "list_audit": {"op", "owner_id", "cursor", "limit"},
}


def valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") == value


def integer_in_range(value: object, low: int, high: int) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and low <= value <= high
        and value == int(value)
    )


def reject_constant(value: str):
    raise ValueError("non-JSON constant")


def parse_command(raw: bytes) -> dict:
    if len(raw) > MAX_INPUT_BYTES:
        raise StorageError("invalid_input")
    try:
        command = json.loads(raw.decode("utf-8", errors="strict"), parse_constant=reject_constant)
        if not isinstance(command, dict):
            raise ValueError
        operation = command.get("op")
        if (
            not isinstance(operation, str)
            or operation not in FIELDS
            or set(command) != FIELDS[operation]
        ):
            raise ValueError
        if operation == "migrate":
            return command
        if not isinstance(command["owner_id"], str) or not IDENTIFIER.fullmatch(
            command["owner_id"]
        ):
            raise ValueError
        if operation == "set_progress":
            if (
                not isinstance(command["lesson_id"], str)
                or not IDENTIFIER.fullmatch(command["lesson_id"])
                or type(command["completed"]) is not bool
                or not valid_timestamp(command["at"])
            ):
                raise ValueError
        else:
            if not integer_in_range(command["cursor"], 0, MAX_CURSOR) or not integer_in_range(
                command["limit"], 1, 50
            ):
                raise ValueError
            command["cursor"] = int(command["cursor"])
            command["limit"] = int(command["limit"])
        return command
    except (ValueError, UnicodeError, OverflowError, RecursionError):
        raise StorageError("invalid_input") from None


def main() -> int:
    try:
        if len(sys.argv) != 2 or not sys.argv[1]:
            raise StorageError("invalid_input")
        command = parse_command(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        result = execute(sys.argv[1], command)
    except StorageError as error:
        print(json.dumps({"error": error.code}, separators=(",", ":")))
        return 1
    except Exception:
        # Neither database paths nor unexpected runtime diagnostics belong in CLI output.
        print('{"error":"storage_failure"}')
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
