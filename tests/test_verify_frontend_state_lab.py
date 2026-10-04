"""Fixed ZIP, mutation, and observation boundaries; no browser or service starts."""

import json
import stat
import sys
import warnings
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_frontend_state_lab as verifier  # noqa: E402

MANIFEST = {
    "id": "frontend-state",
    "version": "frontend-state-v1",
    "lessons": ["fullstack-components", "fullstack-jotai"],
    "languages": ["typescript"],
}


def bundle(path, *, extra=None, missing=None, duplicate=False, symlink=False, manifest=None):
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(verifier.MEMBERS):
            if name == missing:
                continue
            content = json.dumps(manifest or MANIFEST) if name == "manifest.json" else name
            if symlink and name == "src/state.ts":
                member = zipfile.ZipInfo(name)
                member.create_system = 3
                member.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(member, "../../../outside")
            else:
                archive.writestr(name, content)
        if extra:
            archive.writestr(extra, "unexpected")
        if duplicate:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("src/state.ts", "duplicate")
    return path


def test_exact_bundle_extracts_bytes_only_after_validation(tmp_path):
    archive = bundle(tmp_path / "good.zip")
    folder = tmp_path / "unpacked"
    originals = verifier.extract_fixed_archive(archive, folder)
    assert set(originals) == verifier.MEMBERS
    assert (folder / "src/state.ts").read_bytes() == originals["src/state.ts"]
    assert json.loads((folder / "manifest.json").read_text()) == MANIFEST


def test_unexpected_missing_and_duplicate_members_never_partially_extract(tmp_path):
    for index, options in enumerate(
        ({"extra": "../outside"}, {"missing": "src/state.ts"}, {"duplicate": True})
    ):
        archive = bundle(tmp_path / f"bad-{index}.zip", **options)
        target = tmp_path / f"target-{index}"
        with pytest.raises(RuntimeError, match="members"):
            verifier.extract_fixed_archive(archive, target)
        assert not target.exists()
    assert not (tmp_path / "outside").exists()


def test_a_whitelisted_symlink_is_still_refused(tmp_path):
    archive = bundle(tmp_path / "symlink.zip", symlink=True)
    target = tmp_path / "target"
    with pytest.raises(RuntimeError, match="Unsafe"):
        verifier.extract_fixed_archive(archive, target)
    assert not target.exists()


def test_a_different_lab_or_language_is_not_the_fixed_package(tmp_path):
    archive = bundle(tmp_path / "wrong.zip", manifest={**MANIFEST, "languages": ["go"]})
    target = tmp_path / "target"
    with pytest.raises(RuntimeError, match="manifest"):
        verifier.extract_fixed_archive(archive, target)
    assert not target.exists()


def test_mutation_changes_exactly_one_known_expression_or_fails():
    source = f"const completedCountAtom = atom((get) => {verifier.MUTATION});\n"
    assert verifier.make_wrong_count(source) == source.replace(
        verifier.MUTATION, verifier.MUTATION + " + 1"
    )
    for invalid in ("unrelated code", source + source):
        with pytest.raises(RuntimeError, match="missing or ambiguous"):
            verifier.make_wrong_count(invalid)


def test_static_network_allowlist_refuses_business_requests_and_other_origins():
    base = "http://127.0.0.1:49152"
    for path, resource in (
        ("/", "document"),
        ("/assets/index-abc.js", "script"),
        ("/assets/style.css", "stylesheet"),
    ):
        assert verifier.permitted_request(base, base + path, "GET", resource)
    for url, method, resource in (
        (base + "/api/health", "GET", "fetch"),
        (base + "/assets/index.js", "GET", "fetch"),
        (base + "/assets/index.js", "POST", "script"),
        ("http://127.0.0.1:49153/assets/index.js", "GET", "script"),
        ("https://example.invalid/assets/index.js", "GET", "script"),
        ("http://username@127.0.0.1:49152/assets/index.js", "GET", "script"),
    ):
        assert not verifier.permitted_request(base, url, method, resource)
