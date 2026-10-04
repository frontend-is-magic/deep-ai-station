"""Verify the fixed memory-policy ZIP with manual decisions and real CLI exits.

Expected decisions come from the frozen assets and contract, never from importing
or running the product policy engine. The verifier itself uses only stdlib.
"""

# ruff: noqa: S101 - fixed maintainer-owned verification assertions

import copy
import hashlib
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from verify_upload_client import stop_client as stop_owned_command
from verify_upload_storage import stop_direct_process

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "public/labs/memory-policy-python.zip"
VERSION = "memory-policy-v1"
MAX_OUTPUT = 65536
SENTINEL = "private-memory-rejected-input"


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON evidence field")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("Nonfinite JSON evidence")


def decode(raw):
    result = json.loads(
        raw.decode("utf-8"), object_pairs_hook=strict_pairs, parse_constant=reject_constant
    )
    canonical(result)
    return result


def same(actual, expected):
    assert canonical(actual) == canonical(expected), (
        "Memory decision differs from manual expectation"
    )


class Child:
    """Only the fixed direct Python CLI: no worker tree or user commands."""

    def __init__(self, args, cwd, env, *, timeout=8, max_output=MAX_OUTPUT):
        self.process = subprocess.Popen(  # noqa: S603 - fixed package CLI or fixed verifier self-test
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.deadline = time.monotonic() + timeout
        self.max_output = max_output
        self.selector = selectors.DefaultSelector()
        self.output = {"stdout": bytearray(), "stderr": bytearray()}
        self.reaped = False
        for name in self.output:
            self.selector.register(getattr(self.process, name), selectors.EVENT_READ, name)

    def finish(self):
        while self.selector.get_map():
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Memory CLI exceeded its deadline")
            for key, _mask in self.selector.select(min(remaining, 0.05)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                else:
                    self.output[key.data].extend(chunk)
                    if sum(map(len, self.output.values())) > self.max_output:
                        raise RuntimeError("Memory CLI exceeded its output bound")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Memory CLI exceeded its deadline")
        try:
            code = self.process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise TimeoutError("Memory CLI did not exit after closing output") from None
        self.reaped = True
        return code, bytes(self.output["stdout"]), bytes(self.output["stderr"])

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        try:
            stop_direct_process(self.process)
            self.reaped = self.process.returncode is not None
        finally:
            self.selector.close()
            self.process.stdout.close()
            self.process.stderr.close()


def assert_reaped(child):
    assert child.reaped and child.process.returncode is not None
    try:
        os.kill(child.process.pid, 0)
    except ProcessLookupError:
        return
    raise AssertionError("Owned memory CLI still exists")


def snapshot(folder):
    return {
        str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and ".venv" not in path.relative_to(folder).parts
    }


def owned_command(args, cwd, env):
    process = subprocess.Popen(args, cwd=cwd, env=env, start_new_session=True)  # noqa: S603 - fixed locked install/native checks
    try:
        assert process.wait(timeout=600) == 0, "Locked memory lab command failed"
    finally:
        # Install/native commands can own children. Reuse the fixed process-group
        # cleanup which observes live members and never treats EPERM as success.
        stop_owned_command(process)


MEMBERS = {
    "memory_policy.py",
    "loader.py",
    "policy.py",
    "test_loader.py",
    "test_policy.py",
    "test_cli.py",
    "pyproject.toml",
    "uv.lock",
    "memories.json",
    "cases.json",
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "manifest.json",
    ".prettierrc.json",
}
ASSET_SHA = "3cb33e141f61c7729c5a923bc559abe3b41483e301c1366a684b8fe7397b4944"
# Independently assigned from the frozen raw assets before reading any policy implementation.
# selected, visible, owner_mismatch, scope_mismatch, eligible
MANUAL = {
    "normal": (
        {"confirmed-python": "eligible", "project-python": "eligible"},
        (2, 2, 0, 0, 2),
        ("resolved", "python", "memory", ["python"]),
    ),
    "request-override": (
        {"confirmed-python": "eligible"},
        (1, 1, 0, 0, 1),
        ("resolved", "python", "memory", ["python"]),
    ),
    "expiry": (
        {"expires-now": "expired", "future-go": "future"},
        (2, 2, 0, 0, 0),
        ("no_memory", None, None, []),
    ),
    "revoked": (
        {"revoked-go": "revoked", "unconsented-typescript": "unconsented"},
        (2, 2, 0, 0, 0),
        ("no_memory", None, None, []),
    ),
    "scope": (
        {"confirmed-python": "eligible", "knowledge-instruction": "knowledge_only"},
        (6, 2, 2, 2, 1),
        ("resolved", "python", "memory", ["python"]),
    ),
    "conflict": (
        {"confirmed-python": "eligible", "conflicting-go": "eligible"},
        (2, 2, 0, 0, 2),
        ("conflict", None, None, ["go", "python"]),
    ),
}
FOREIGN = (
    "foreign-owner-preference",
    "foreign-scope-preference",
    "foreign-owner-knowledge",
    "foreign-scope-knowledge",
    "other-owner-source",
    "other-scope-source",
    "foreign-owner-source-fixture",
    "foreign-scope-source-fixture",
    "foreign-owner-private-text-fixture",
    "foreign-scope-private-text-fixture",
)


def asset_hash(values):
    ordered = copy.deepcopy(values)
    for name in ("sources", "memories"):
        ordered["memories"][name].sort(key=lambda item: item["id"])
    ordered["cases"]["cases"].sort(key=lambda item: item["id"])
    for case in ordered["cases"]["cases"]:
        case["memory_ids"].sort()
    return hashlib.sha256(canonical(ordered)).hexdigest()


def common(values, command):
    return {
        "contract_version": VERSION,
        "lesson_id": "agent-memory",
        "model_calls": 0,
        "read_only": True,
        "assets_sha256": asset_hash(values),
        "command": command,
    }


def expected_list(values):
    return {
        **common(values, "list"),
        "cases": [
            {"id": case["id"], "title": case["title"]}
            for case in sorted(values["cases"]["cases"], key=lambda item: item["id"])
        ],
    }


def expected_case(values, case_id, language=None, resolution=None):
    # Values supply exact original record text only. Qualification/counts/decision
    # are the explicit hand-audited table, never a second policy implementation.
    case = next(case for case in values["cases"]["cases"] if case["id"] == case_id)
    memories = {item["id"]: item for item in values["memories"]["memories"]}
    titles = {source["id"]: source["title"] for source in values["memories"]["sources"]}
    decisions, counts, base_resolution = MANUAL[case_id]
    status, chosen, source, candidates = resolution or base_resolution
    if language is not None:
        status, chosen, source = "resolved", language, "request"
    return {
        **common(values, "resolve"),
        "case_id": case_id,
        "as_of": case["as_of"],
        "context": {"owner": case["owner"], "scope": case["scope"], "request_language": language},
        "counts": dict(
            zip(
                ("selected", "visible", "owner_mismatch", "scope_mismatch", "eligible"),
                counts,
                strict=True,
            )
        ),
        "records": [
            {
                "memory": memories[record_id],
                "source_title": titles[memories[record_id]["source"]],
                "decision": decisions[record_id],
            }
            for record_id in sorted(decisions)
        ],
        "resolution": {
            "status": status,
            "language": chosen,
            "source": source,
            "candidate_languages": candidates,
        },
    }


def error(code):
    return {"contract_version": VERSION, "error": code}


def check_report(code, stdout, stderr, expected, exit_code):
    assert code == exit_code, "Memory CLI exit status differs"
    assert not stderr, "Memory CLI emitted diagnostics"
    assert stdout.endswith(b"\n") and stdout.count(b"\n") == 1, "Expected one UTF-8 JSON line"
    assert SENTINEL.encode() not in stdout
    assert all(value.encode() not in stdout for value in FOREIGN), "Foreign record content leaked"
    result = decode(stdout)
    same(result, expected)
    return result


def extract(package):
    package.mkdir()
    with zipfile.ZipFile(ARCHIVE) as archive:
        entries = archive.infolist()
        assert len(entries) == len(MEMBERS) and {entry.filename for entry in entries} == MEMBERS, (
            "Unexpected ZIP members"
        )
        assert sum(entry.file_size for entry in entries) <= 2_000_000
        assert all(
            not entry.is_dir() and (entry.external_attr >> 16) & 0o170000 != 0o120000
            for entry in entries
        )
        archive.extractall(package)
    same(
        decode((package / "manifest.json").read_bytes()),
        {
            "id": "memory-policy",
            "version": VERSION,
            "lessons": ["agent-memory"],
            "languages": ["python"],
        },
    )
    values = {
        name: decode((package / (name + ".json")).read_bytes()) for name in ("memories", "cases")
    }
    assert asset_hash(values) == ASSET_SHA, (
        "Packaged teaching assets differ from manually audited assets"
    )
    assert sorted(case["id"] for case in values["cases"]["cases"]) == sorted(MANUAL)
    return values


class Lab:
    def __init__(self, python, package, cwd, env):
        self.python, self.package, self.cwd, self.env = python, package, cwd, env
        self.children, self.scenarios = [], []
        self.deadline = time.monotonic() + 120

    def child(self, args, package):
        remaining = self.deadline - time.monotonic()
        assert remaining > 0, "Memory verification exceeded its total deadline"
        child = Child(
            [str(self.python), "-s", "-B", "-m", "memory_policy", *args],
            self.cwd,
            {**self.env, "PYTHONPATH": str(package)},
            timeout=min(8, remaining),
        )
        self.children.append(child)
        return child

    def call(self, *args, expected, exit_code=0, package=None):
        folder = package or self.package
        before, cwd_before = snapshot(folder), snapshot(self.cwd)
        with self.child(args, folder) as child:
            result = check_report(*child.finish(), expected, exit_code)
        assert_reaped(child)
        assert snapshot(folder) == before and snapshot(self.cwd) == cwd_before, (
            "Read-only CLI changed files"
        )
        return result

    def clean(self):
        for child in self.children:
            assert_reaped(child)


def write_assets(folder, values):
    for name, value in values.items():
        (folder / (name + ".json")).write_bytes(canonical(value) + b"\n")


def copy_package(package, root, name):
    folder = root / name
    folder.mkdir()
    for filename in MEMBERS:
        shutil.copyfile(package / filename, folder / filename)
    return folder


def successful_cases(lab, values, root):
    lab.call("--list", expected=expected_list(values))
    for case_id in sorted(MANUAL):
        lab.call("--case", case_id, expected=expected_case(values, case_id))
    for case_id, language in (
        ("request-override", "go"),
        ("conflict", "typescript"),
        ("expiry", "go"),
    ):
        lab.call(
            "--language",
            language,
            "--case",
            case_id,
            expected=expected_case(values, case_id, language),
        )
    lab.scenarios.append("six-manual-cases-list-and-three-request-overrides")

    reordered = copy.deepcopy(values)
    reordered["memories"]["sources"].reverse()
    reordered["memories"]["memories"].reverse()
    reordered["cases"]["cases"].reverse()
    for case in reordered["cases"]["cases"]:
        case["memory_ids"].reverse()
    folder = copy_package(lab.package, root, "reordered")
    write_assets(folder, reordered)
    lab.call("--list", expected=expected_list(values), package=folder)
    for case_id in ("scope", "conflict"):
        lab.call("--case", case_id, expected=expected_case(values, case_id), package=folder)
    lab.scenarios.append("array-order-and-json-layout-do-not-change-output-or-fingerprint")

    changed = copy.deepcopy(values)
    next(item for item in changed["memories"]["memories"] if item["id"] == "confirmed-python")[
        "language"
    ] = "go"
    assert asset_hash(changed) != ASSET_SHA
    folder = copy_package(lab.package, root, "valid-counterfactual")
    write_assets(folder, changed)
    for case_id, resolution in (
        ("normal", ("conflict", None, None, ["go", "python"])),
        ("conflict", ("resolved", "go", "memory", ["go"])),
    ):
        lab.call(
            "--case",
            case_id,
            expected=expected_case(changed, case_id, resolution=resolution),
            package=folder,
        )
    lab.scenarios.append("valid-record-change-switches-two-decisions-without-case-id-switching")


def rejected_inputs(lab):
    for args in (
        (),
        ("--help",),
        ("--case", SENTINEL),
        ("--case", "normal", "--case", "scope"),
        ("--list", "--language", "go"),
        ("--case=normal",),
        ("--case", "normal", "--language", "ruby"),
        ("--case",),
    ):
        lab.call(*args, expected=error("invalid_input"), exit_code=1)
    lab.scenarios.append("strict-cli-flags-values-and-unknown-case")


def rejected_assets(lab, values, root):
    raw = (lab.package / "memories.json").read_bytes()
    replacements = {
        "invalid-utf8": b"\x80",
        "duplicate-key": b'{"version":"memory-records-v1",' + raw.lstrip()[1:],
        "overflow-number": canonical(values["memories"]).replace(
            b'"consent":true', b'"consent":1e999', 1
        ),
        "oversized": b" " * 65537,
    }
    for name, payload in replacements.items():
        assert payload != raw
        folder = copy_package(lab.package, root, name)
        (folder / "memories.json").write_bytes(payload)
        lab.call("--case", "normal", expected=error("assets_invalid"), exit_code=1, package=folder)
    lab.scenarios.append("bounded-whole-asset-json-rejection")

    changed = copy.deepcopy(values)
    next(
        item for item in changed["memories"]["memories"] if item["id"] == "foreign-owner-knowledge"
    )["source"] = "missing-source"
    folder = copy_package(lab.package, root, "unreferenced-corruption")
    write_assets(folder, changed)
    lab.call(
        "--case",
        "normal",
        "--language",
        "go",
        expected=error("assets_invalid"),
        exit_code=1,
        package=folder,
    )
    lab.call("--case", SENTINEL, expected=error("assets_invalid"), exit_code=1, package=folder)
    lab.call("--unknown", SENTINEL, expected=error("invalid_input"), exit_code=1, package=folder)
    lab.scenarios.append("unused-foreign-record-validation-and-cli-error-priority")

    changed = copy.deepcopy(values)
    changed["memories"]["version"] = "future-memory-version"
    folder = copy_package(lab.package, root, "future-version")
    write_assets(folder, changed)
    lab.call("--list", expected=error("incompatible_version"), exit_code=1, package=folder)
    lab.scenarios.append("future-asset-version-refused")

    parent = root / "resource-fallback"
    parent.mkdir()
    folder = copy_package(lab.package, parent, "python")
    shared = parent / "shared"
    shared.mkdir()
    write_assets(shared, values)
    (folder / "cases.json").unlink()
    shared_before = snapshot(shared)
    lab.call("--case", "normal", expected=error("assets_invalid"), exit_code=1, package=folder)
    (folder / "memories.json").unlink()
    lab.call("--case", "normal", expected=expected_case(values, "normal"), package=folder)
    assert snapshot(shared) == shared_before
    lab.scenarios.append("partial-local-assets-cannot-fallback-but-absent-pair-can")


def closed_output(lab):
    before = snapshot(lab.package)
    with lab.child(("--case", "normal"), lab.package) as child:
        child.selector.unregister(child.process.stdout)
        child.process.stdout.close()
        code, stdout, stderr = child.finish()
        assert code == 1 and stdout == stderr == b"", "Closed stdout must exit quietly"
    assert snapshot(lab.package) == before
    lab.scenarios.append("closed-stdout-exits-without-traceback")


def verify():
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required on PATH")
    location = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="deep-ai-memory-verify-", dir=location) as temporary:
        root = Path(temporary).resolve()
        assert not root.is_relative_to(ROOT)
        package = root / "package"
        values = extract(package)
        install = {
            key: os.environ[key]
            for key in ("PATH", "UV_CACHE_DIR", "TMPDIR", "CI")
            if key in os.environ
        }
        home = root / "home"
        home.mkdir()
        install.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
        owned_command([uv, "sync", "--frozen", "--python", "3.12"], package, install)
        for command in (
            ("ruff", "check", "."),
            ("ruff", "format", "--check", "."),
            ("pytest", "-q"),
        ):
            owned_command([uv, "run", "--frozen", *command], package, install)
        cwd = root / "unrelated-cwd"
        cwd.mkdir()
        for name in ("memories.json", "cases.json"):
            (cwd / name).write_text(SENTINEL)
        runtime = {
            "PATH": os.defpath,
            "HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "17",
            "LANG": "C.UTF-8",
        }
        lab = Lab(package / ".venv/bin/python", package, cwd, runtime)
        try:
            successful_cases(lab, values, root)
            rejected_inputs(lab)
            rejected_assets(lab, values, root)
            closed_output(lab)
        finally:
            lab.clean()
        result = {
            "lab": VERSION,
            "archive_members": len(MEMBERS),
            "manual_cases": len(MANUAL),
            "asset_sha256": ASSET_SHA,
            "scenarios": lab.scenarios,
            "cli_processes_reaped": len(lab.children),
            "native_checks": "passed",
            "source_and_assets_unchanged_by_cli": True,
            "model_calls": 0,
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    verify()
