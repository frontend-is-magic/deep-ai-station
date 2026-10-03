"""Two deterministic splitters over raw Unicode codepoints, not tokens."""

import re

from loader import LabError, canonical, digest, identifier, revision

SPLITTER_VERSION = "markdown-boundary-v1"
ATX = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*)|$)")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")


def configurations(strategy: str, size: int, overlap: int) -> dict:
    if strategy == "heading":
        return {}
    if (
        strategy != "window"
        or type(size) is not int
        or type(overlap) is not int
        or not 32 <= size <= 256
        or not 0 <= overlap <= 16
        or overlap >= size
    ):
        raise LabError("invalid_input")
    return {"size": size, "overlap": overlap}


def headings(text: str) -> list[tuple[int, list[str]]]:
    """Recognize only the documented ATX/fenced-code subset, preserving offsets."""
    result = []
    stack: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    # Unlike splitlines(), this does not treat U+2028 as a Markdown line separator.
    for match in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n|$)", text):
        line = match.group().rstrip("\r\n")
        marker = FENCE.fullmatch(line)
        if fence is not None:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= fence[1]
                and not marker[2].strip(" \t")
            ):
                fence = None
            continue
        if marker and (marker[1][0] == "~" or "`" not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            continue
        title = ATX.fullmatch(line)
        if title is None:
            continue
        level = len(title[1])
        value = title[2] or ""
        value = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", value).strip(" \t")
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, value))
        result.append((match.start(), [name for _, name in stack]))
    return result


def chunk_sources(corpus: dict, strategy: str, *, size: int = 80, overlap: int = 16) -> list[dict]:
    configuration = configurations(strategy, size, overlap)
    chunks = []
    for source in corpus["sources"]:
        text = source["text"]
        if not text:
            continue
        titles = headings(text)
        if strategy == "heading":
            starts = [0] + [offset for offset, _ in titles if offset != 0]
            ranges = list(zip(starts, starts[1:] + [len(text)], strict=True))
        else:
            ranges = []
            for start in range(0, len(text), size - overlap):
                end = min(start + size, len(text))
                ranges.append((start, end))
                if end == len(text):
                    break
        for start, end in ranges:
            path = []
            for title_start, header_path in titles:
                if title_start > start:
                    break
                path = header_path
            identity = {
                "splitter_version": SPLITTER_VERSION,
                "strategy": strategy,
                "configuration": configuration,
                "source_id": source["source_id"],
                "source_revision": source["source_revision"],
                "start": start,
                "end": end,
            }
            chunks.append(
                {
                    "chunk_id": digest(canonical(identity)),
                    "source_id": source["source_id"],
                    "source_uri": source["source_uri"],
                    "source_revision": source["source_revision"],
                    "start": start,
                    "end": end,
                    "byte_start": len(text[:start].encode("utf-8")),
                    "byte_end": len(text[:end].encode("utf-8")),
                    "header_path": list(path),
                    "text": text[start:end],
                }
            )
    return chunks


def quote_source(corpus: dict, source_id: str, source_revision: str, start: int, end: int) -> dict:
    if (
        not identifier(source_id)
        or not revision(source_revision)
        or type(start) is not int
        or type(end) is not int
        or not 0 <= start <= 2147483647
        or not 0 <= end <= 2147483647
    ):
        raise LabError("invalid_input")
    source = next((item for item in corpus["sources"] if item["source_id"] == source_id), None)
    if source is None:
        raise LabError("source_not_found")
    if source["source_revision"] != source_revision:
        raise LabError("revision_mismatch")
    text = source["text"]
    if not start < end <= len(text) or end - start > 512:
        raise LabError("invalid_range")
    return {
        "source_id": source_id,
        "source_uri": source["source_uri"],
        "source_revision": source_revision,
        "start": start,
        "end": end,
        "byte_start": len(text[:start].encode("utf-8")),
        "byte_end": len(text[:end].encode("utf-8")),
        "quote": text[start:end],
    }
