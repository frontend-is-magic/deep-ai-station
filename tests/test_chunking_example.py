"""Prevent ambiguous identities in the maintained fixed chunking reference."""

import ast

import pytest

from backend.examples import AGENT_EXAMPLES


@pytest.fixture
def chunks():
    source = ast.parse(AGENT_EXAMPLES["chunking"])
    nodes = [
        node
        for node in source.body
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))
    ]
    namespace = {}
    exec(  # noqa: S102 - repository-owned teaching snippet only
        compile(ast.Module(body=nodes, type_ignores=[]), "chunking-reference", "exec"), namespace
    )
    return namespace["chunks"]


def test_repeated_content_has_distinct_ids_and_roundtrips_source_ranges(chunks):
    source = "A😀éZ。" * 4
    result = list(chunks(source, size=6, overlap=0))
    assert len(result) == 4 and len({item["text"] for item in result}) == 1
    assert len({item["id"] for item in result}) == 4
    assert "".join(item["text"] for item in result) == source
    assert [(item["start"], item["end"]) for item in result] == [
        (0, 6),
        (6, 12),
        (12, 18),
        (18, 24),
    ]
    assert all(len(item["id"]) == 71 for item in result)


def test_source_revision_configuration_and_replay_define_identity(chunks):
    original = list(chunks("abcdef", size=3, overlap=0, source_id="a"))
    assert list(chunks("abcdef", size=3, overlap=0, source_id="a")) == original
    other_source = list(chunks("abcdef", size=3, overlap=0, source_id="b"))[0]
    changed_tail = list(chunks("abcxyz", size=3, overlap=0, source_id="a"))[0]
    changed_overlap = list(chunks("abcdef", size=3, overlap=1, source_id="a"))[0]
    assert {
        original[0]["text"],
        other_source["text"],
        changed_tail["text"],
        changed_overlap["text"],
    } == {"abc"}
    assert (
        len({item["id"] for item in [original[0], other_source, changed_tail, changed_overlap]})
        == 4
    )
    assert original[0]["source_revision"] != changed_tail["source_revision"]


def test_empty_source_and_exact_tail_do_not_make_an_empty_chunk(chunks):
    assert list(chunks("")) == []
    assert [item["text"] for item in chunks("abcdef", size=3, overlap=0)] == ["abc", "def"]
    with pytest.raises(ValueError, match="invalid_chunk_window"):
        list(chunks("text", size=3, overlap=3))
