"""Read only the bundled, pinned teaching assets; no caller-selected paths."""

import hashlib
import json
import math
import re
from pathlib import Path

CONTRACT_VERSION = "document-chunking-v1"
CORPUS_PIN = "sha256:67e12f61bc6954699784b5d28df09c1cabcc973230e3e42b58282e16e74a4d06"
DATASET_PIN = "sha256:3eed9ec3443603fca295bca70a5f7bb755c8bc22c1f496497754930837eb427b"
ASSET_DIRECTORY = Path(__file__).resolve().parent
MAX_ASSET_BYTES = 65536
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,63}")
REVISION = re.compile(r"sha256:[0-9a-f]{64}")


class LabError(Exception):
    """Fixed public error codes, without diagnostic payloads."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def require(condition: bool) -> None:
    if not condition:
        raise LabError("corpus_invalid")


def object_fields(value: object, fields: set[str]) -> None:
    require(type(value) is dict and set(value) == fields)


def text(value: object, maximum: int = 16384) -> bool:
    return type(value) is str and 0 < len(value) <= maximum


def identifier(value: object) -> bool:
    return type(value) is str and IDENTIFIER.fullmatch(value) is not None


def revision(value: object) -> bool:
    return type(value) is str and REVISION.fullmatch(value) is not None


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def invalid_constant(_: str) -> None:
    raise LabError("corpus_invalid")


def validate_json_tree(value: object, depth: int = 0) -> None:
    if type(value) in (dict, list):
        require(depth < 32)
        if type(value) is dict:
            for key, item in value.items():
                key.encode("utf-8", "strict")
                validate_json_tree(item, depth + 1)
        else:
            for item in value:
                validate_json_tree(item, depth + 1)
    elif type(value) is str:
        value.encode("utf-8", "strict")
    elif type(value) is float:
        require(math.isfinite(value))


def read_asset(path: Path) -> dict:
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_ASSET_BYTES + 1)
        require(len(raw) <= MAX_ASSET_BYTES)
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
        validate_json_tree(value)
        require(type(value) is dict)
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise LabError("corpus_invalid") from None


def validate_corpus(corpus: dict) -> None:
    object_fields(
        corpus,
        {"corpus_id", "corpus_version", "corpus_revision", "coordinate_unit", "sources"},
    )
    require(
        all(text(corpus[key], 100) for key in ("corpus_id", "corpus_version", "coordinate_unit"))
    )
    require(revision(corpus["corpus_revision"]))
    sources = corpus["sources"]
    require(type(sources) is list and len(sources) == 3)
    ids = set()
    for source in sources:
        object_fields(
            source,
            {
                "source_id",
                "source_uri",
                "source_revision",
                "title",
                "kind",
                "provenance",
                "references",
                "text",
            },
        )
        require(identifier(source["source_id"]) and source["source_id"] not in ids)
        ids.add(source["source_id"])
        require(source["source_uri"] == "course://chunking/" + source["source_id"])
        require(revision(source["source_revision"]) and text(source["text"]))
        require(digest(source["text"].encode("utf-8")) == source["source_revision"])
        require(text(source["title"], 100) and text(source["kind"], 100))
        require(type(source["references"]) is list and len(source["references"]) <= 5)
        require(all(text(url, 2000) and url.startswith("https://") for url in source["references"]))
        require(type(source["provenance"]) is list and 1 <= len(source["provenance"]) <= 5)
        for origin in source["provenance"]:
            object_fields(origin, {"path", "id", "field"})
            require(all(text(value, 100) for value in origin.values()))
    identity = [
        {key: source[key] for key in ("source_id", "source_uri", "source_revision")}
        for source in sources
    ]
    require(digest(canonical(identity)) == corpus["corpus_revision"])


def validate_dataset(dataset: dict, corpus: dict) -> None:
    object_fields(
        dataset,
        {"dataset_id", "dataset_version", "corpus_revision", "dataset_revision", "cases"},
    )
    require(text(dataset["dataset_id"], 100) and text(dataset["dataset_version"], 100))
    require(dataset["corpus_revision"] == corpus["corpus_revision"])
    require(revision(dataset["dataset_revision"]))
    cases = dataset["cases"]
    require(type(cases) is list and len(cases) == 6)
    sources = {source["source_id"]: source for source in corpus["sources"]}
    case_ids, gold_ids = set(), set()
    for case in cases:
        object_fields(case, {"id", "query", "question", "gold"})
        require(identifier(case["id"]) and case["id"] not in case_ids)
        case_ids.add(case["id"])
        require(text(case["query"], 200) and text(case["question"], 500))
        require(type(case["gold"]) is list and len(case["gold"]) <= 3)
        for gold in case["gold"]:
            object_fields(
                gold, {"gold_id", "source_id", "source_revision", "start", "end", "quote"}
            )
            require(identifier(gold["gold_id"]) and gold["gold_id"] not in gold_ids)
            gold_ids.add(gold["gold_id"])
            require(identifier(gold["source_id"]) and gold["source_id"] in sources)
            source = sources[gold["source_id"]]
            require(gold["source_revision"] == source["source_revision"])
            require(type(gold["start"]) is int and type(gold["end"]) is int)
            require(0 <= gold["start"] < gold["end"] <= len(source["text"]))
            require(text(gold["quote"], 512))
            require(source["text"][gold["start"] : gold["end"]] == gold["quote"])
    require(digest(canonical(cases)) == dataset["dataset_revision"])


def load_assets() -> tuple[dict, dict]:
    corpus = read_asset(ASSET_DIRECTORY / "corpus.json")
    dataset = read_asset(ASSET_DIRECTORY / "cases.json")
    validate_corpus(corpus)
    validate_dataset(dataset, corpus)
    if (
        corpus["corpus_id"] != "agent-chunking-course-notes"
        or corpus["corpus_version"] != "chunking-corpus-v1"
        or corpus["coordinate_unit"] != "unicode_codepoint"
        or dataset["dataset_id"] != "chunking-evidence"
        or dataset["dataset_version"] != "chunking-evidence-v1"
        or digest(canonical(corpus)) != CORPUS_PIN
        or digest(canonical(dataset)) != DATASET_PIN
    ):
        raise LabError("incompatible_version")
    return corpus, dataset
