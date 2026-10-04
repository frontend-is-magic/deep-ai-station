"""Strict loading of the three complete, immutable teaching assets."""

import hashlib
import json
import math
from pathlib import Path

EXPERIMENT_VERSION = "output-regression-v1"
CORPUS_REVISION = "agent-corpus-v1"
DATASET_VERSION = "output-regression-cases-v1"
PROFILES_VERSION = "synthetic-outputs-v1"
MEASUREMENT_MODE = "synthetic-output-replay"
GATE_VERSION = "critical-no-regression-v1"
PROFILE_IDS = ("baseline", "unsafe-candidate", "fixed-candidate")
CASE_IDS = (
    "dev-api",
    "dev-empty",
    "dev-conflict",
    "accept-api",
    "accept-tools",
    "accept-evidence",
    "accept-conflict",
    "accept-empty",
    "accept-found-unread",
    "accept-unicode",
    "accept-two-sources",
)
PINS = {
    "corpus.json": "dd45d5e4b4d4e694d1bf25909e6355a3844e5b063b709c6fc3a00371cf3396bb",
    "cases.json": "5b778e6a9e436275b006e4daca35fd8aa7d29dd97077ce0f36937db17d17060f",
    "profiles.json": "7991e90ffa8a98f8c9f070b02fc5c156e571c907715a94033581cb0069ef2f6d",
}
ASSET_LIMIT = 2_000_000
OUTPUT_LIMIT = 65_536
MAX_DEPTH = 32


class LabError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_json")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("invalid_json")


def strict_json(raw: str | bytes, limit: int):
    """Reject nonfinite numbers and Unicode problems without coercing a valid JSON root."""
    try:
        if isinstance(raw, bytes):
            if len(raw) > limit:
                raise ValueError
            text = raw.decode("utf-8", "strict")
        elif isinstance(raw, str):
            if len(raw.encode("utf-8", "strict")) > limit:
                raise ValueError
            text = raw
        else:
            raise ValueError
        depth, quoted, escaped = 0, False, False
        for character in text:
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
            elif character == '"':
                quoted = True
            elif character in "[{":
                depth += 1
                if depth > MAX_DEPTH:
                    raise ValueError
            elif character in "]}":
                depth -= 1
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError
            elif isinstance(item, str):
                item.encode("utf-8", "strict")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid_json") from None


def require(condition):
    if not condition:
        raise LabError("assets_invalid")


def shape(value, keys):
    return isinstance(value, dict) and set(value) == set(keys)


def text(value, maximum=20000):
    return isinstance(value, str) and 0 < len(value) <= maximum and bool(value.strip())


def version(value, expected):
    require(isinstance(value, str))
    if value != expected:
        raise LabError("incompatible_version")


def validate_versions(dataset, profiles):
    require(isinstance(dataset, dict) and isinstance(profiles, dict))
    version(dataset.get("dataset_version"), DATASET_VERSION)
    version(dataset.get("corpus_revision"), CORPUS_REVISION)
    version(profiles.get("profiles_version"), PROFILES_VERSION)
    version(profiles.get("measurement_mode"), MEASUREMENT_MODE)
    require(isinstance(profiles.get("profiles"), list))
    for profile in profiles["profiles"]:
        require(isinstance(profile, dict))
        version(profile.get("revision"), "v1")


def valid_ids(values, allowed):
    return (
        isinstance(values, list)
        and all(isinstance(value, str) and value in allowed for value in values)
        and len(set(values)) == len(values)
    )


def validate_assets(corpus, dataset, profiles):
    validate_versions(dataset, profiles)
    require(isinstance(corpus, list) and len(corpus) == 5)
    source_ids = []
    for document in corpus:
        require(isinstance(document, dict))
        conflict = document.get("kind") == "conflict-fixture"
        keys = {"id", "title", "keywords", "body", "url", "kind"}
        require(shape(document, keys | ({"conflict_group"} if conflict else set())))
        require(all(text(document[key]) for key in ("id", "title", "body")))
        require(document["kind"] in ("course-excerpt", "conflict-fixture"))
        require(
            valid_ids(document["keywords"], document["keywords"]) and bool(document["keywords"])
        )
        require(all(text(word, 100) for word in document["keywords"]))
        require(
            (conflict and document["url"] is None and text(document["conflict_group"]))
            or (
                not conflict
                and text(document["url"], 2000)
                and document["url"].startswith("https://")
            )
        )
        source_ids.append(document["id"])
    require(source_ids == ["api", "tools", "evidence", "billing-a", "billing-b"])
    require(shape(dataset, {"dataset_version", "corpus_revision", "cases"}))
    require(isinstance(dataset["cases"], list) and len(dataset["cases"]) == len(CASE_IDS))
    for index, case in enumerate(dataset["cases"]):
        require(
            shape(
                case,
                {
                    "id",
                    "split",
                    "prompt",
                    "found_ids",
                    "read_ids",
                    "required_citation_ids",
                    "critical",
                },
            )
        )
        require(case["id"] == CASE_IDS[index])
        require(case["split"] == ("dev" if index < 3 else "acceptance"))
        require(text(case["prompt"], 1000) and type(case["critical"]) is bool)
        for key in ("found_ids", "read_ids", "required_citation_ids"):
            require(valid_ids(case[key], source_ids))
        require(set(case["read_ids"]) <= set(case["found_ids"]))
        require(set(case["required_citation_ids"]) <= set(case["read_ids"]))
        if {"billing-a", "billing-b"} & set(case["found_ids"]):
            require({"billing-a", "billing-b"} <= set(case["required_citation_ids"]))
    require(shape(profiles, {"profiles_version", "measurement_mode", "profiles"}))
    require(len(profiles["profiles"]) == len(PROFILE_IDS))
    for index, profile in enumerate(profiles["profiles"]):
        require(shape(profile, {"id", "revision", "outputs"}))
        require(profile["id"] == PROFILE_IDS[index])
        require(isinstance(profile["outputs"], list) and len(profile["outputs"]) == len(CASE_IDS))
        for output_index, output in enumerate(profile["outputs"]):
            require(shape(output, {"case_id", "raw_output"}))
            require(output["case_id"] == CASE_IDS[output_index])
            # Inner invalid JSON is intentional test data, not a broken asset.
            require(isinstance(output["raw_output"], str))
            require(len(output["raw_output"].encode("utf-8", "strict")) <= OUTPUT_LIMIT)


def load_assets(directory: Path | None = None) -> tuple[list, dict, dict]:
    root = directory or Path(__file__).resolve().parent
    raw_assets = {}
    try:
        for name in PINS:
            with (root / name).open("rb") as stream:
                raw_assets[name] = stream.read(ASSET_LIMIT + 1)
    except OSError:
        raise LabError("assets_unavailable") from None
    try:
        corpus, dataset, profiles = [strict_json(raw_assets[name], ASSET_LIMIT) for name in PINS]
    except ValueError:
        raise LabError("assets_invalid") from None
    validate_assets(corpus, dataset, profiles)
    require(all(sha256(raw_assets[name]) == expected for name, expected in PINS.items()))
    return corpus, dataset, profiles


def metadata() -> dict:
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "measurement_mode": MEASUREMENT_MODE,
        "read_only": True,
        "model_calls": 0,
        "corpus_revision": CORPUS_REVISION,
        "corpus_sha256": PINS["corpus.json"],
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": PINS["cases.json"],
        "profiles_version": PROFILES_VERSION,
        "profiles_sha256": PINS["profiles.json"],
        "gate_version": GATE_VERSION,
    }
