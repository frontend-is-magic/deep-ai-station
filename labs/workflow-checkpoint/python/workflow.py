"""Fixed pure workflow. Persisted payload validation is separate from node execution."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

VERSION = "workflow-checkpoint-v1"
CORPUS_REVISION = "agent-corpus-v1"
CORPUS_SHA256 = "3d22e8986d9c33832b2d4604dc27d431d8caaa21b5344f79f40065a0301f48b1"
NODES = ("retrieve", "draft", "validate")
PHASES = ("ready", "retrieved", "drafted", "completed")
CASES = {
    "normal": {"query": "API 超时"},
    "empty": {"query": "zzzz unmatched"},
    "conflict": {"query": "取消计费冲突"},
}
ANSWERS = {
    "normal": "API 调用需要明确输入、错误类别和超时预算。",
    "empty": "固定资料中没有匹配证据。",
    "conflict": "合成资料对取消计费存在冲突，不能据此给出一致结论。",
}
OUTCOMES = {
    "normal": "complete",
    "empty": "insufficient_evidence",
    "conflict": "conflicting_evidence",
}


class LabError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.performed: list[str] = []


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8", "strict")).hexdigest()


def valid_run_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except ValueError:
        return False


def reject_constant(_value):
    raise ValueError


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def parse_json(raw: str, limit: int = 16384):
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8", "strict")) > limit:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
        canonical(value).encode("utf-8", "strict")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise LabError("checkpoint_invalid") from None


def load_fixtures(path: Path | None = None) -> list[dict]:
    try:
        raw = (path or Path(__file__).with_name("fixtures.json")).read_text(encoding="utf-8")
        data = parse_json(raw)
        if (
            set(data) != {"documents", "cases"}
            or data["cases"] != CASES
            or digest(data["documents"]) != CORPUS_SHA256
        ):
            raise ValueError
        return data["documents"]
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, LabError):
        raise LabError("incompatible_version") from None


def read_document(document: dict) -> dict:
    return {
        key: document.get(key) for key in ("id", "title", "body", "url", "kind", "conflict_group")
    }


def retrieved_output(case_id: str, documents: list[dict]) -> dict:
    query = CASES[case_id]["query"]
    found_ids = [
        document["id"]
        for document in documents
        if any(keyword.casefold() in query.strip().casefold() for keyword in document["keywords"])
    ]
    # A search hit is not itself a read: explicitly resolve each selected source.
    reads = [
        read_document(next(doc for doc in documents if doc["id"] == found)) for found in found_ids
    ]
    return {"query": query, "found_ids": found_ids, "read_documents": reads}


def drafted_output(case_id: str, retrieval: dict) -> dict:
    citations = []
    for document in retrieval["read_documents"]:
        quote = (
            "API 明确输入长度、错误类别和超时预算。"
            if document["id"] == "api"
            else document["body"]
        )
        citations.append({"source_id": document["id"], "quote": quote, "url": document["url"]})
    return {"answer": ANSWERS[case_id], "citations": citations}


def validate_citations(case_id: str, retrieval: dict, draft: dict) -> None:
    """Mechanical source binding; this is not a semantic truth evaluator."""
    try:
        reads = {document["id"]: document for document in retrieval["read_documents"]}
        seen = set()
        for citation in draft["citations"]:
            source = citation["source_id"]
            document = reads[source]
            if (
                source in seen
                or not citation["quote"]
                or citation["quote"] not in document["body"]
                or citation["url"] != document["url"]
            ):
                raise ValueError
            seen.add(source)
        required = {"normal": {"api"}, "empty": set(), "conflict": {"billing-a", "billing-b"}}
        if seen != required[case_id]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise LabError("checkpoint_invalid") from None


def validated_output(case_id: str, retrieval: dict, draft: dict) -> dict:
    validate_citations(case_id, retrieval, draft)
    return {"outcome": OUTCOMES[case_id], **draft, "read_only": True, "model_calls": 0}


def validate_saved_outputs(case_id: str, outputs: list[dict], documents: list[dict]) -> None:
    # Revalidate stored state, without invoking execute_node or committing new work.
    expected = []
    if outputs:
        expected.append(retrieved_output(case_id, documents))
    if len(outputs) > 1:
        expected.append(drafted_output(case_id, expected[0]))
    if len(outputs) > 2:
        expected.append(validated_output(case_id, expected[0], expected[1]))
    # Canonical JSON distinguishes bool from int (Python equality does not).
    if canonical(outputs) != canonical(expected):
        raise LabError("checkpoint_invalid")


def execute_node(node: str, case_id: str, outputs: list[dict], documents: list[dict]) -> dict:
    if node == "retrieve":
        return retrieved_output(case_id, documents)
    if node == "draft":
        return drafted_output(case_id, outputs[0])
    if node == "validate":
        return validated_output(case_id, outputs[0], outputs[1])
    raise LabError("checkpoint_invalid")


def checkpoint_hash(run: dict, revision: int, node: str, output: dict, previous_hash) -> str:
    return digest(
        {
            "run_id": run["run_id"],
            "revision": revision,
            "node": node,
            "workflow_version": run["workflow_version"],
            "corpus_revision": run["corpus_revision"],
            "corpus_sha256": run["corpus_sha256"],
            "previous_hash": previous_hash,
            "output": output,
        }
    )
