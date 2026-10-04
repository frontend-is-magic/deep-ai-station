"""Bounded validation of both complete, fixed-path teaching assets."""

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

CONTRACT_VERSION = "memory-policy-v1"
LANGUAGES = ("go", "python", "typescript")
ASSET_LIMIT = 65_536
MAX_DEPTH = 16
ASSET_NAMES = ("memories.json", "cases.json")
ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,63}")
DATE_PATTERN = re.compile(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")


class LabError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def require(condition):
    if not condition:
        raise LabError("assets_invalid")


def shape(value, keys):
    return type(value) is dict and set(value) == set(keys)


def valid_text(value, maximum=500):
    if type(value) is not str or not 0 < len(value) <= maximum or not value.strip():
        return False
    try:
        value.encode("utf-8", "strict")
    except UnicodeError:
        return False
    return all(ord(char) >= 32 and ord(char) != 127 for char in value)


def valid_id(value):
    return type(value) is str and ID_PATTERN.fullmatch(value) is not None


def valid_date(value):
    if type(value) is not str or DATE_PATTERN.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") == value
    except ValueError:
        return False


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError


def strict_json(raw: bytes):
    try:
        if type(raw) is not bytes or len(raw) > ASSET_LIMIT:
            raise ValueError
        text = raw.decode("utf-8", "strict")
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > MAX_DEPTH:
                    raise ValueError
            elif char in "]}":
                depth -= 1
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
        pending = [value]
        while pending:
            item = pending.pop()
            if type(item) is dict:
                pending.extend(item.keys())
                pending.extend(item.values())
            elif type(item) is list:
                pending.extend(item)
            elif type(item) is float and not math.isfinite(item):
                raise ValueError
            elif type(item) is str:
                item.encode("utf-8", "strict")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise LabError("assets_invalid") from None


def validate_version(value, expected):
    require(valid_text(value, 100))
    if value != expected:
        raise LabError("incompatible_version")


def validate_memory(record, source_ids):
    require(type(record) is dict)
    kind = record.get("kind")
    require(kind in ("preference", "knowledge"))
    keys = {
        "id",
        "owner",
        "scope",
        "kind",
        "source",
        "updated_at",
        "expires_at",
        "consent",
        "revoked",
    }
    require(shape(record, keys | ({"language"} if kind == "preference" else {"text"})))
    require(all(valid_id(record[key]) for key in ("id", "owner", "scope", "source")))
    require(record["source"] in source_ids)
    if kind == "preference":
        require(type(record["language"]) is str and record["language"] in LANGUAGES)
    else:
        require(valid_text(record["text"]))
    require(type(record["consent"]) is bool and type(record["revoked"]) is bool)
    require(valid_date(record["updated_at"]))
    expires = record["expires_at"]
    require(expires is None or (valid_date(expires) and expires >= record["updated_at"]))


def unique_items(value, maximum):
    require(type(value) is list and 1 <= len(value) <= maximum)
    require(all(type(item) is dict and valid_id(item.get("id")) for item in value))
    require(len({item["id"] for item in value}) == len(value))


def validate_assets(memories, cases):
    require(shape(memories, {"version", "sources", "memories"}))
    require(shape(cases, {"version", "cases"}))
    validate_version(memories["version"], "memory-records-v1")
    validate_version(cases["version"], "memory-cases-v1")
    unique_items(memories["sources"], 16)
    for source in memories["sources"]:
        require(shape(source, {"id", "title"}) and valid_text(source["title"], 100))
    sources = {source["id"] for source in memories["sources"]}
    unique_items(memories["memories"], 64)
    for record in memories["memories"]:
        validate_memory(record, sources)
    ids = {record["id"] for record in memories["memories"]}
    unique_items(cases["cases"], 8)
    for case in cases["cases"]:
        require(shape(case, {"id", "title", "owner", "scope", "as_of", "memory_ids"}))
        require(valid_text(case["title"], 100))
        require(valid_id(case["owner"]) and valid_id(case["scope"]) and valid_date(case["as_of"]))
        selected = case["memory_ids"]
        require(type(selected) is list and 1 <= len(selected) <= 64)
        require(all(valid_id(value) and value in ids for value in selected))
        require(len(set(selected)) == len(selected))


def asset_digest(memories, cases):
    normalized = {
        "memories": {
            "version": memories["version"],
            "sources": sorted(memories["sources"], key=lambda item: item["id"]),
            "memories": sorted(memories["memories"], key=lambda item: item["id"]),
        },
        "cases": {
            "version": cases["version"],
            "cases": sorted(
                [{**case, "memory_ids": sorted(case["memory_ids"])} for case in cases["cases"]],
                key=lambda item: item["id"],
            ),
        },
    }
    return hashlib.sha256(canonical(normalized).encode("utf-8")).hexdigest()


def load_assets(directory: Path | None = None):
    root = directory if directory is not None else Path(__file__).resolve().parent
    try:
        if directory is None and not any((root / name).exists() for name in ASSET_NAMES):
            root = root.parent / "shared"
        assets = []
        for name in ASSET_NAMES:
            with (root / name).open("rb") as stream:
                assets.append(strict_json(stream.read(ASSET_LIMIT + 1)))
    except OSError:
        raise LabError("assets_invalid") from None
    validate_assets(*assets)
    return tuple(assets)
