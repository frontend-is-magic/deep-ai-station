import json
from copy import deepcopy

import pytest

from loader import ASSET_LIMIT, LabError, asset_digest, load_assets, strict_json, validate_assets


@pytest.fixture
def assets():
    return load_assets()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"id":1,"id":2}',
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b"1e999",
        b'"\\ud800"',
        b'"\xff"',
        b"\xef\xbb\xbf{}",
        b"{}{}",
        b"[" * 17 + b"0" + b"]" * 17,
        b" " * (ASSET_LIMIT + 1),
        b"9" * 5000,
    ],
)
def test_strict_json_rejects_ambiguous_or_unbounded_input(raw):
    with pytest.raises(LabError, match="assets_invalid"):
        strict_json(raw)


def test_depth_guard_ignores_quoted_braces_and_preserves_unicode():
    text = '["' + "[" * 100 + '😀é\\"' + '"]'
    assert strict_json(text.encode()) == ["[" * 100 + '😀é"']


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "wrong-type",
        "duplicate-record",
        "bad-source",
        "duplicate-reference",
        "missing-reference",
        "invalid-date",
        "not-milliseconds",
        "expires-before-update",
        "extra-kind-field",
        "bad-id",
        "control-text",
        "unreferenced-corrupt",
    ],
)
def test_complete_assets_fail_closed_on_broken_shape_or_relation(assets, mutation):
    memories, cases = assets
    first = memories["memories"][0]
    if mutation == "extra":
        memories["private"] = "private-input-fixture"
    elif mutation == "wrong-type":
        first["consent"] = 1
    elif mutation == "duplicate-record":
        memories["memories"].append(deepcopy(first))
    elif mutation == "bad-source":
        first["source"] = "unknown"
    elif mutation == "duplicate-reference":
        cases["cases"][0]["memory_ids"].append(cases["cases"][0]["memory_ids"][0])
    elif mutation == "missing-reference":
        cases["cases"][0]["memory_ids"] = ["unknown"]
    elif mutation == "invalid-date":
        first["updated_at"] = "2026-02-30T12:00:00.000Z"
    elif mutation == "not-milliseconds":
        first["updated_at"] = "2026-10-03T12:00:00Z"
    elif mutation == "expires-before-update":
        first["expires_at"] = "2026-10-03T11:59:59.999Z"
    elif mutation == "extra-kind-field":
        first["text"] = "ignored?"
    elif mutation == "bad-id":
        first["id"] = "confirmed-python\n"
    elif mutation == "control-text":
        memories["sources"][0]["title"] = "has\x00control"
    else:
        memories["memories"].append({**first, "id": "unreferenced", "language": "rust"})
    with pytest.raises(LabError, match="assets_invalid"):
        validate_assets(memories, cases)


@pytest.mark.parametrize(
    ("version", "code"),
    [
        ("memory-records-v2", "incompatible_version"),
        ("", "assets_invalid"),
        (None, "assets_invalid"),
    ],
)
def test_version_has_fixed_error_class(assets, version, code):
    assets[0]["version"] = version
    with pytest.raises(LabError, match=code):
        validate_assets(*assets)


def test_asset_loader_rejects_missing_and_oversize_without_raw_diagnostics(tmp_path, assets):
    for name, asset in zip(("memories.json", "cases.json"), assets, strict=True):
        (tmp_path / name).write_text(json.dumps(asset))
    assert load_assets(tmp_path) == assets
    (tmp_path / "cases.json").unlink()
    with pytest.raises(LabError, match="^assets_invalid$"):
        load_assets(tmp_path)
    (tmp_path / "cases.json").write_bytes(b" " * (ASSET_LIMIT + 1))
    with pytest.raises(LabError, match="^assets_invalid$"):
        load_assets(tmp_path)


def test_semantic_fingerprint_matches_independently_frozen_assets(assets):
    assert (
        asset_digest(*assets) == "3cb33e141f61c7729c5a923bc559abe3b41483e301c1366a684b8fe7397b4944"
    )
