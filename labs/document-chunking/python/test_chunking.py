"""Original coordinates and scope identity are independent of strategy quality."""

import hashlib
import json

import pytest

from chunking import chunk_sources, headings, quote_source
from loader import LabError, load_assets


def document(text, source_id="sample"):
    return {
        "source_id": source_id,
        "source_uri": "course://chunking/" + source_id,
        "source_revision": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
    }


def split(text, strategy="heading", **kwargs):
    return chunk_sources({"sources": [document(text)]}, strategy, **kwargs)


def test_heading_partition_hierarchy_mixed_newlines_and_raw_bytes():
    text = "preamble\r\n# Top\r\nbody\n### Deep ###\r## Next\t ##\nplain"
    chunks = split(text)
    assert [c["text"] for c in chunks] == [
        "preamble\r\n",
        "# Top\r\nbody\n",
        "### Deep ###\r",
        "## Next\t ##\nplain",
    ]
    assert [c["header_path"] for c in chunks] == [[], ["Top"], ["Top", "Deep"], ["Top", "Next"]]
    assert "".join(c["text"] for c in chunks) == text
    for chunk in chunks:
        assert text[chunk["start"] : chunk["end"]] == chunk["text"]
        assert text.encode()[chunk["byte_start"] : chunk["byte_end"]].decode() == chunk["text"]


@pytest.mark.parametrize(
    "line,path",
    [
        ("# A", ["A"]),
        ("   ## A ### \t", ["A"]),
        ("#\tA", ["A"]),
        ("###### A", ["A"]),
        ("#", [""]),
        ("# ###", [""]),
        ("## ### ###", ["###"]),
        ("# A#", ["A#"]),
        ("#x", []),
        ("####### A", []),
        ("    # A", []),
        ("> # A", []),
        ("- # A", []),
        ("A\n===", []),
        ("prefix\u2028# A", []),
    ],
)
def test_atx_subset_and_empty_title(line, path):
    observed = headings(line)
    assert (observed[-1][1] if observed else []) == path


@pytest.mark.parametrize(
    "text,expected",
    [
        ("# top\n```py\n# hidden\n```\n## shown\n", [["top"], ["top", "shown"]]),
        (
            "# top\n~~~~lang\n## hidden\n~~~\n# hidden2\n~~~~~ \n## shown",
            [["top"], ["top", "shown"]],
        ),
        ("# top\n~~~\n# hidden\n```\n## hidden2", [["top"]]),
        ("# top\n```\n# hidden\n```suffix\n## hidden2", [["top"]]),
        ("# top\n```bad`info\n## shown", [["top"], ["top", "shown"]]),
        ("# top\n    ```\n## shown", [["top"], ["top", "shown"]]),
        ("# top\r```\r# hidden\r```\r## shown", [["top"], ["top", "shown"]]),
    ],
)
def test_fences_do_not_create_false_headings(text, expected):
    assert [path for _, path in headings(text)] == expected
    assert "".join(c["text"] for c in split(text)) == text


@pytest.mark.parametrize(
    "text", ["", "   \r\n", "no heading", "# only", "# A\n# B\n", "# 长节\n" + "正文" * 400]
)
def test_empty_preamble_and_long_heading_never_drop_or_secretly_resplit(text):
    chunks = split(text)
    assert "".join(c["text"] for c in chunks) == text
    assert all(c["start"] < c["end"] for c in chunks)
    if len(text) > 256:
        assert len(chunks) == 1 and len(chunks[0]["text"]) > 256
    if not text:
        assert chunks == []


@pytest.mark.parametrize(
    "length,size,overlap",
    [
        (0, 32, 0),
        (32, 32, 0),
        (64, 32, 16),
        (81, 80, 16),
        (160, 80, 0),
        (233, 80, 16),
        (700, 256, 16),
    ],
)
def test_windows_cover_source_overlap_exactly_and_stop_at_end(length, size, overlap):
    text = ("A😀e\u0301\r\nZ" * 101)[:length]
    chunks = split(text, "window", size=size, overlap=overlap)
    if not text:
        assert chunks == []
        return
    assert chunks[0]["start"] == 0 and chunks[-1]["end"] == len(text)
    for previous, following in zip(chunks, chunks[1:], strict=False):
        assert previous["end"] - following["start"] == overlap
    for chunk in chunks:
        assert chunk["text"] == text[chunk["start"] : chunk["end"]]
        assert text.encode()[chunk["byte_start"] : chunk["byte_end"]].decode() == chunk["text"]
        assert 0 < chunk["end"] - chunk["start"] <= size


@pytest.mark.parametrize(
    "kwargs",
    [
        {"size": 31},
        {"size": 257},
        {"overlap": 17},
        {"overlap": -1},
        {"size": True},
        {"overlap": False},
        {"size": 80.0},
    ],
)
def test_invalid_window_configuration(kwargs):
    with pytest.raises(LabError, match="^invalid_input$"):
        split("hello", "window", **kwargs)


def test_ids_bind_source_revision_position_strategy_and_actual_configuration():
    repeated = "# Same\nbody\n" * 2
    sources = [document(repeated, "one"), document(repeated, "two")]
    chunks = chunk_sources({"sources": sources}, "heading")
    assert len({c["text"] for c in chunks}) == 1
    assert len({c["chunk_id"] for c in chunks}) == 4
    first = chunks[0]
    payload = {
        "splitter_version": "markdown-boundary-v1",
        "strategy": "heading",
        "configuration": {},
        "source_id": "one",
        "source_revision": sources[0]["source_revision"],
        "start": 0,
        "end": len("# Same\nbody\n"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert first["chunk_id"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    changed = chunk_sources({"sources": [document(repeated + "changed", "one")]}, "heading")[0]
    assert changed["text"] == first["text"] and changed["chunk_id"] != first["chunk_id"]
    small = "# Short\n"
    variants = [
        split(small)[0],
        split(small, "window", size=80)[0],
        split(small, "window", size=81)[0],
    ]
    assert len({c["chunk_id"] for c in variants}) == 3


def test_window_path_is_metadata_not_prepended_and_returned_lists_are_detached():
    chunks = split(
        "# Parent\n" + "x" * 24 + "\n## Child\n" + "y" * 50, "window", size=32, overlap=0
    )
    assert chunks[1]["header_path"] == ["Parent"]
    assert "## Child" in chunks[1]["text"]
    chunks[0]["header_path"].append("changed")
    assert chunks[1]["header_path"] == ["Parent"]


def test_quote_unicode_crlf_and_version_identity():
    corpus, _ = load_assets()
    source = corpus["sources"][1]
    quote = quote_source(corpus, "tool-evidence", source["source_revision"], 172, 178)
    assert quote["quote"] == "A😀e\u0301Z。"
    assert (quote["byte_start"], quote["byte_end"]) == (442, 454)
    assert quote_source(corpus, "tool-evidence", source["source_revision"], 7, 9)["quote"] == "\r\n"


@pytest.mark.parametrize(
    "source,revision,start,end,code",
    [
        ("unknown", "sha256:" + "0" * 64, 0, 1, "source_not_found"),
        ("api-guide", "sha256:" + "0" * 64, 0, 1, "revision_mismatch"),
        ("api-guide", None, 0, 0, "invalid_range"),
        ("api-guide", None, 10, 9, "invalid_range"),
        ("api-guide", None, 0, 98, "invalid_range"),
        ("api-guide", None, True, 2, "invalid_input"),
    ],
)
def test_quote_errors(source, revision, start, end, code):
    corpus, _ = load_assets()
    revision = revision or corpus["sources"][0]["source_revision"]
    with pytest.raises(LabError, match="^" + code + "$"):
        quote_source(corpus, source, revision, start, end)


def test_quote_limit_is_checked_even_for_long_pure_function_source():
    corpus = {"sources": [document("x" * 600)]}
    with pytest.raises(LabError, match="^invalid_range$"):
        quote_source(corpus, "sample", corpus["sources"][0]["source_revision"], 0, 513)
