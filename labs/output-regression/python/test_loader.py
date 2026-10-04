"""Asset integrity and parser resource boundaries use temporary copies only."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from loader import ASSET_LIMIT, PINS, LabError, load_assets, strict_json, validate_assets


@pytest.fixture
def asset_dir(tmp_path):
    source = Path(__file__).resolve().parent
    for name in PINS:
        (tmp_path / name).write_bytes((source / name).read_bytes())
    return tmp_path


def change(directory, name, mutate):
    path = directory / name
    value = json.loads(path.read_bytes())
    mutate(value)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def assert_error(directory, code):
    with pytest.raises(LabError) as error:
        load_assets(directory)
    assert error.value.code == code
    assert str(error.value) == code


def test_all_three_frozen_assets_load_and_remain_byte_identical(asset_dir):
    before = {name: (asset_dir / name).read_bytes() for name in PINS}
    corpus, dataset, profiles = load_assets(asset_dir)
    assert len(corpus) == 5
    assert len(dataset["cases"]) == 11
    assert [profile["id"] for profile in profiles["profiles"]] == [
        "baseline",
        "unsafe-candidate",
        "fixed-candidate",
    ]
    for name, expected in PINS.items():
        assert hashlib.sha256(before[name]).hexdigest() == expected
        assert (asset_dir / name).read_bytes() == before[name]


@pytest.mark.parametrize("name", list(PINS))
def test_missing_any_asset_rejects_without_partial_result(asset_dir, name):
    (asset_dir / name).unlink()
    assert_error(asset_dir, "assets_unavailable")


@pytest.mark.parametrize(
    "raw",
    [
        b"\x80",
        b"\xef\xbb\xbf{}",
        b'{"x":1,"x":2}',
        b'{"x":{"y":1,"y":2}}',
        b'{"x":1e999}',
        b'{"x":NaN}',
        b'{"x":"\\ud800"}',
        b'{"\\udfff":0}',
        b"[" * 33 + b"0" + b"]" * 33,
        b"x" * (ASSET_LIMIT + 1),
        b"{} {}",
    ],
)
def test_invalid_outer_json_and_limits_are_assets_invalid(asset_dir, raw):
    (asset_dir / "cases.json").write_bytes(raw)
    assert_error(asset_dir, "assets_invalid")


@pytest.mark.parametrize("name", list(PINS))
def test_whitespace_only_asset_edits_still_break_raw_pin(asset_dir, name):
    path = asset_dir / name
    path.write_bytes(path.read_bytes() + b" ")
    assert_error(asset_dir, "assets_invalid")


@pytest.mark.parametrize(
    ("filename", "field"),
    [
        ("cases.json", "dataset_version"),
        ("cases.json", "corpus_revision"),
        ("profiles.json", "profiles_version"),
        ("profiles.json", "measurement_mode"),
    ],
)
def test_unknown_string_version_precedes_extra_field_and_pin(asset_dir, filename, field):
    def mutate(value):
        value[field] = "future-v9"
        value["extra"] = True

    change(asset_dir, filename, mutate)
    assert_error(asset_dir, "incompatible_version")


@pytest.mark.parametrize("value", [None, 1, True, [], {}])
def test_version_wrong_type_is_asset_error(asset_dir, value):
    change(asset_dir, "cases.json", lambda data: data.update(dataset_version=value))
    assert_error(asset_dir, "assets_invalid")


def test_missing_version_and_profile_version_errors(asset_dir):
    original = (asset_dir / "profiles.json").read_bytes()
    change(asset_dir, "profiles.json", lambda data: data["profiles"][-1].update(revision="v2"))
    assert_error(asset_dir, "incompatible_version")
    (asset_dir / "profiles.json").write_bytes(original)
    change(asset_dir, "profiles.json", lambda data: data["profiles"][-1].pop("revision"))
    assert_error(asset_dir, "assets_invalid")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["cases"].pop(0),
        lambda data: data["cases"].append(copy.deepcopy(data["cases"][0])),
        lambda data: data["cases"][0].update(id="accept-api"),
        lambda data: data["cases"].reverse(),
        lambda data: data["cases"][0].update(split="acceptance"),
        lambda data: data["cases"][0].update(read_ids=["tools"]),
        lambda data: data["cases"][0].update(required_citation_ids=["tools"]),
        lambda data: data["cases"][0].update(found_ids=["api", "api"]),
        lambda data: data["cases"][0].update(found_ids=["unknown"]),
        lambda data: data["cases"][0].update(critical=1),
        lambda data: data["cases"][0].update(extra=True),
        lambda data: data["cases"][2].update(required_citation_ids=["billing-a"]),
    ],
)
def test_case_alignment_and_relations_are_checked_across_all_splits(asset_dir, mutation):
    change(asset_dir, "cases.json", mutation)
    assert_error(asset_dir, "assets_invalid")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["profiles"].pop(),
        lambda data: data["profiles"].reverse(),
        lambda data: data["profiles"][-1]["outputs"].pop(0),
        lambda data: data["profiles"][-1]["outputs"].reverse(),
        lambda data: data["profiles"][-1]["outputs"][0].update(case_id="accept-api"),
        lambda data: data["profiles"][-1]["outputs"][0].update(raw_output=True),
        lambda data: data["profiles"][-1]["outputs"][0].update(raw_output="a" * 65537),
        lambda data: data["profiles"][-1]["outputs"][0].update(extra=1),
        lambda data: data["profiles"][-1].update(extra=1),
    ],
)
def test_profile_must_contain_every_original_case_exactly_once(asset_dir, mutation):
    change(asset_dir, "profiles.json", mutation)
    assert_error(asset_dir, "assets_invalid")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.pop(),
        lambda data: data.reverse(),
        lambda data: data[0].update(body=""),
        lambda data: data[0].update(url=None),
        lambda data: data[0].update(keywords=["x", "x"]),
        lambda data: data[0].update(extra="x"),
        lambda data: data[-1].update(url="https://example.invalid/"),
    ],
)
def test_source_shape_is_validated_before_pin(asset_dir, mutation):
    change(asset_dir, "corpus.json", mutation)
    assert_error(asset_dir, "assets_invalid")


def test_invalid_inner_output_is_valid_asset_content_not_outer_parse_error():
    corpus, dataset, profiles = load_assets()
    modified = copy.deepcopy(profiles)
    modified["profiles"][0]["outputs"][0]["raw_output"] = '{"answer":NaN}'
    validate_assets(corpus, dataset, modified)
    # CLI loading still rejects any byte edit by pin; direct validation separates layers.


def test_parser_depth_and_string_brackets_are_not_confused():
    assert strict_json("[" * 32 + "0" + "]" * 32, 1000) == nest(32)
    string = '[[[}}} escaped quote " and slash \\ and ]]]'
    assert strict_json(json.dumps({"text": string}), 1000) == {"text": string}


def nest(count):
    value = 0
    for _ in range(count):
        value = [value]
    return value


def test_default_integer_digit_protection_is_not_disabled():
    with pytest.raises(ValueError, match="^invalid_json$"):
        strict_json("9" * 5000, 65536)
    assert strict_json("9" * 100, 65536) == int("9" * 100)


def test_parser_byte_limit_and_valid_surrogate_pair():
    assert strict_json('"😀"', 6) == "😀"
    assert strict_json('"\\ud83d\\ude00"', 100) == "😀"
    with pytest.raises(ValueError, match="^invalid_json$"):
        strict_json('"😀"', 5)


def test_unreadable_asset_maps_to_unavailable_without_environment_details(asset_dir):
    path = asset_dir / "corpus.json"
    path.unlink()
    path.mkdir()
    assert_error(asset_dir, "assets_unavailable")
