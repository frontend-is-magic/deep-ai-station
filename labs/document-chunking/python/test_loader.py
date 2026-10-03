"""Asset tampering must not become a valid empty experiment."""

import json
import shutil
from pathlib import Path

import pytest

import loader
from loader import LabError, canonical, digest, load_assets, read_asset

BASE = Path(__file__).parent


@pytest.fixture
def assets(tmp_path, monkeypatch):
    for name in ("corpus.json", "cases.json"):
        shutil.copyfile(BASE / name, tmp_path / name)
    monkeypatch.setattr(loader, "ASSET_DIRECTORY", tmp_path)
    return tmp_path


def write_asset(directory, name, value):
    (directory / name).write_bytes(canonical(value))


def test_actual_sources_gold_unicode_bytes_and_pins():
    corpus, dataset = load_assets()
    assert [
        (s["source_id"], len(s["text"]), len(s["text"].encode())) for s in corpus["sources"]
    ] == [("api-guide", 97, 249), ("tool-evidence", 233, 551), ("billing-conflict", 79, 195)]
    expected = [
        (20, 41, 38, 93),
        (31, 81, 75, 197),
        (103, 144, 249, 372),
        (172, 178, 442, 454),
        (21, 37, 45, 89),
        (46, 78, 102, 194),
    ]
    gold = [g for case in dataset["cases"] for g in case["gold"]]
    sources = {s["source_id"]: s for s in corpus["sources"]}
    assert len(gold) == 6
    for span, (start, end, byte_start, byte_end) in zip(gold, expected, strict=True):
        text = sources[span["source_id"]]["text"]
        assert (span["start"], span["end"]) == (start, end)
        assert text[start:end] == span["quote"]
        assert text.encode()[byte_start:byte_end].decode() == span["quote"]
    text = sources["tool-evidence"]["text"]
    assert text.count("\r\n") == 14
    assert [ord(c) for c in text[172:178]] == [0x41, 0x1F600, 0x65, 0x301, 0x5A, 0x3002]
    assert digest(canonical(corpus)) == loader.CORPUS_PIN
    assert digest(canonical(dataset)) == loader.DATASET_PIN


@pytest.mark.parametrize(
    "raw",
    [
        b"\x80",
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e999}',
        b'{"x":"\\ud800"}',
        b'{"\\udfff":1}',
        b"{",
        b"[]",
        b'{"x":' + b"[" * 32 + b"0" + b"]" * 32 + b"}",
    ],
)
def test_invalid_json_unicode_depth_and_finite_values(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(LabError, match="^corpus_invalid$"):
        read_asset(path)


def test_asset_bytes_limit_and_container_depth_exact_boundaries(tmp_path):
    path = tmp_path / "asset.json"
    path.write_bytes(b"{}" + b" " * (65536 - 2))
    assert read_asset(path) == {}
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(LabError, match="^corpus_invalid$"):
        read_asset(path)
    path.write_bytes(b'{"x":' + b"[" * 31 + b"0" + b"]" * 31 + b"}")
    assert type(read_asset(path)["x"]) is list


@pytest.mark.parametrize("name", ["corpus.json", "cases.json"])
def test_missing_asset_is_fixed_error(assets, name):
    (assets / name).unlink()
    with pytest.raises(LabError, match="^corpus_invalid$"):
        load_assets()


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "bad-body",
        "bool-range",
        "bad-quote",
        "wrong-source",
        "duplicate-gold",
        "invalid-type",
    ],
)
def test_structural_and_self_description_corruption(assets, mutation):
    corpus = json.loads((assets / "corpus.json").read_text())
    dataset = json.loads((assets / "cases.json").read_text())
    if mutation == "extra":
        corpus["unexpected"] = "private-input-fixture"
    elif mutation == "bad-body":
        corpus["sources"][0]["text"] += "modified"
    elif mutation == "bool-range":
        dataset["cases"][0]["gold"][0]["start"] = True
    elif mutation == "bad-quote":
        dataset["cases"][0]["gold"][0]["quote"] = "not actual quote"
    elif mutation == "wrong-source":
        dataset["cases"][0]["gold"][0]["source_id"] = "unknown"
    elif mutation == "duplicate-gold":
        dataset["cases"][1]["gold"][0]["gold_id"] = dataset["cases"][0]["gold"][0]["gold_id"]
    else:
        corpus["sources"][0]["references"] = True
    write_asset(assets, "corpus.json", corpus)
    write_asset(assets, "cases.json", dataset)
    with pytest.raises(LabError, match="^corpus_invalid$"):
        load_assets()


@pytest.mark.parametrize(
    "filename,field,value",
    [
        ("corpus.json", "corpus_version", "future"),
        ("corpus.json", "coordinate_unit", "utf16"),
        ("cases.json", "dataset_version", "future"),
    ],
)
def test_unknown_version_rejected_without_repair(assets, filename, field, value):
    asset = json.loads((assets / filename).read_text())
    asset[field] = value
    write_asset(assets, filename, asset)
    before = {name: (assets / name).read_bytes() for name in ("corpus.json", "cases.json")}
    with pytest.raises(LabError, match="^incompatible_version$"):
        load_assets()
    assert before == {name: (assets / name).read_bytes() for name in before}


def test_recomputed_consistent_question_drift_still_fails_fixed_pin(assets):
    dataset = json.loads((assets / "cases.json").read_text())
    dataset["cases"][0]["query"] = "changed query"
    dataset["dataset_revision"] = digest(canonical(dataset["cases"]))
    write_asset(assets, "cases.json", dataset)
    with pytest.raises(LabError, match="^incompatible_version$"):
        load_assets()


def test_recomputed_consistent_source_drift_still_fails_fixed_pin(assets):
    corpus = json.loads((assets / "corpus.json").read_text())
    dataset = json.loads((assets / "cases.json").read_text())
    source = corpus["sources"][0]
    source["text"] += "\n"
    source["source_revision"] = digest(source["text"].encode())
    corpus["corpus_revision"] = digest(
        canonical(
            [
                {k: s[k] for k in ("source_id", "source_uri", "source_revision")}
                for s in corpus["sources"]
            ]
        )
    )
    dataset["corpus_revision"] = corpus["corpus_revision"]
    dataset["cases"][0]["gold"][0]["source_revision"] = source["source_revision"]
    dataset["dataset_revision"] = digest(canonical(dataset["cases"]))
    write_asset(assets, "corpus.json", corpus)
    write_asset(assets, "cases.json", dataset)
    with pytest.raises(LabError, match="^incompatible_version$"):
        load_assets()
