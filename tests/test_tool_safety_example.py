"""Behavior of the maintained teaching snippet, never user-submitted source."""

import ast

import pytest

from backend.examples import AGENT_EXAMPLES


@pytest.fixture
def prepare_write():
    source = ast.parse(AGENT_EXAMPLES["tool-safety"])
    functions = [
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "prepare_write"
    ]
    assert len(functions) == 1
    # Run only this repository-owned function, excluding the display examples.
    namespace = {}
    exec(  # noqa: S102 - fixed maintainer-owned reference source, not user input
        compile(ast.Module(body=functions, type_ignores=[]), "tool-safety-reference", "exec"),
        namespace,
    )
    return namespace["prepare_write"]


def test_unauthorized_calls_never_read_or_change_receipts(prepare_write):
    class UnavailableReceipts(dict):
        def __contains__(self, key):
            raise AssertionError("authorization must precede receipt lookup")

        def __getitem__(self, key):
            raise AssertionError("unauthorized receipt read")

        def __setitem__(self, key, value):
            raise AssertionError("unauthorized receipt write")

    original = {"write-1": {"operation_id": "write-1", "status": "applied"}}
    receipts = UnavailableReceipts(original)
    for operation_id in ("write-1", "new-write"):
        for confirmed in (False, True):
            assert prepare_write(operation_id, False, confirmed, receipts) == {
                "error": "tool_not_authorized"
            }
    assert receipts == original


def test_new_operation_without_confirmation_leaves_existing_receipts_unchanged(prepare_write):
    existing = {"operation_id": "existing", "status": "applied"}
    receipts = {"existing": existing}
    assert prepare_write("write-1", True, False, receipts) == {"status": "needs_confirmation"}
    assert receipts == {"existing": existing}


def test_authorized_confirmed_write_creates_exactly_one_receipt(prepare_write):
    receipts = {}
    receipt = prepare_write("write-1", True, True, receipts)
    assert receipt == {"operation_id": "write-1", "status": "applied"}
    assert receipts == {"write-1": receipt}


def test_authorized_replay_does_not_require_a_new_confirmation_or_add_a_receipt(prepare_write):
    receipts = {}
    original = prepare_write("write-1", True, True, receipts)
    replayed = prepare_write("write-1", True, False, receipts)
    assert replayed == {"status": "reused", "receipt": original}
    assert replayed["receipt"] is original
    assert receipts == {"write-1": original}
