"""Fixed local teaching assets and two real, read-only tools."""

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ASSET_VERSION = "agent-loop-corpus-v1"
DOCUMENT_IDS = ("loop-guide", "tool-guide")
CASE_IDS = ("found", "empty")
DOCUMENT_FIELDS = {"id", "title", "body", "source", "kind"}


class AssetsError(Exception):
    """The complete fixed asset could not be loaded or validated."""


def valid_text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def valid_document(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != DOCUMENT_FIELDS:
        return False
    if not all(valid_text(item) for item in value.values()):
        return False
    if value["id"] not in DOCUMENT_IDS or value["kind"] != "teaching-summary":
        return False
    try:
        source = urlsplit(value["source"])
        return (
            source.scheme == "https"
            and bool(source.hostname)
            and source.username is None
            and source.password is None
            and not any(char.isspace() for char in value["source"])
            and (source.port is None or 1 <= source.port <= 65535)
        )
    except ValueError:
        return False


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite number")
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite number")


def _asset_bytes() -> bytes:
    adjacent = Path(__file__).with_name("fixtures.json")
    try:
        return adjacent.read_bytes()
    except FileNotFoundError:
        # A dangling link is present but invalid; only an absent file permits fallback.
        try:
            adjacent.lstat()
        except FileNotFoundError:
            return (adjacent.parent.parent / "shared" / "fixtures.json").read_bytes()
        raise AssetsError from None


def load_assets() -> dict[str, Any]:
    try:
        asset = json.loads(
            _asset_bytes().decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
        if not isinstance(asset, dict) or set(asset) != {"asset_version", "documents", "cases"}:
            raise ValueError("asset shape")
        if asset["asset_version"] != ASSET_VERSION:
            raise ValueError("asset version")
        documents, cases = asset["documents"], asset["cases"]
        if not isinstance(documents, list) or len(documents) != 2:
            raise ValueError("documents")
        for expected_id, document in zip(DOCUMENT_IDS, documents, strict=True):
            if not isinstance(document, dict) or set(document) != DOCUMENT_FIELDS | {"keywords"}:
                raise ValueError("document shape")
            projection = {key: value for key, value in document.items() if key != "keywords"}
            keywords = document["keywords"]
            if not valid_document(projection) or document["id"] != expected_id:
                raise ValueError("document")
            if (
                not isinstance(keywords, list)
                or not keywords
                or not all(valid_text(keyword) for keyword in keywords)
                or len(set(keywords)) != len(keywords)
            ):
                raise ValueError("keywords")
        if not isinstance(cases, list) or len(cases) != 2:
            raise ValueError("cases")
        for expected_id, case in zip(CASE_IDS, cases, strict=True):
            if (
                not isinstance(case, dict)
                or set(case) != {"id", "query"}
                or case["id"] != expected_id
                or not valid_text(case["query"])
            ):
                raise ValueError("case")
        return asset
    except (OSError, ValueError, TypeError, RecursionError, AssetsError):
        raise AssetsError from None


def assets_sha256(asset: dict[str, Any]) -> str:
    canonical = json.dumps(asset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LocalTools:
    def __init__(self, asset: dict[str, Any]):
        self._documents = copy.deepcopy(asset["documents"])

    def search(self, query: str) -> dict[str, Any]:
        normalized = query.strip().casefold()
        found = [
            document["id"]
            for document in self._documents
            if any(keyword.casefold() in normalized for keyword in document["keywords"])
        ]
        return {"type": "search_result", "query": query, "found_ids": found}

    def read(self, document_id: str) -> dict[str, Any]:
        for document in self._documents:
            if document["id"] == document_id:
                return {
                    "type": "read_result",
                    "document": {
                        key: value for key, value in document.items() if key != "keywords"
                    },
                }
        raise ValueError("unknown document")
