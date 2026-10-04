"""Verify the regression ZIP using manual gold, actual CLI exits, and owned cleanup.

Never imports a product parser/evaluator to derive expected CLI results.
"""

# ruff: noqa: S101 - assertions specify fixed maintainer-owned evidence

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

from verify_course_labs import stop_owned_process_group

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "public/labs/output-regression-python.zip"
VERSION = "output-regression-v1"
SENTINEL = "private-regression-sentinel"
MAX_OUTPUT = 262144
ASSET_PINS = {
    "corpus.json": "dd45d5e4b4d4e694d1bf25909e6355a3844e5b063b709c6fc3a00371cf3396bb",
    "cases.json": "5b778e6a9e436275b006e4daca35fd8aa7d29dd97077ce0f36937db17d17060f",
    "profiles.json": "7991e90ffa8a98f8c9f070b02fc5c156e571c907715a94033581cb0069ef2f6d",
}
BODY_SHA = {
    "api": "79084866a61caf6899896c15cb58dcfe21cc012180d60e049fb17db3e6e0b0e9",
    "tools": "ea084236e90b05c4ef5dae39c8b0eb8883e5bbb068cc2cd20b66befb0c795485",
    "evidence": "ef81e09a57dd756170dd46dfa762f3188bbaf231fabb215c3cd50b0040e60b31",
    "billing-a": "47ce3772530e78bded167eab9556aee9b75eca66a0c0c8ce9b5f534c230c5dda",
    "billing-b": "21bfcc0347a715268d6773a2a561afd30ac1d1aadec8292a414b26009486d8d3",
}
PROFILES = ("baseline", "unsafe-candidate", "fixed-candidate")
# Assigned by reading raw strings and source bodies before any evaluator existed.
# Columns are baseline / unsafe / fixed. None means all three layers passed.
MANUAL_CODES = {
    "dev-api": ("invalid_json", None, None),
    "dev-empty": (None, None, None),
    "dev-conflict": (None, None, None),
    "accept-api": ("invalid_json", None, None),
    "accept-tools": ("invalid_schema", None, None),
    "accept-evidence": ("invalid_quote", None, None),
    "accept-conflict": (None, None, None),
    "accept-empty": (None, None, None),
    "accept-found-unread": (None, "unread_citation", None),
    "accept-unicode": (None, None, None),
    "accept-two-sources": (None, None, None),
}
MANUAL_COUNTS = {
    ("dev", "baseline"): (3, 2, 2, 2, 2, 2),
    ("dev", "unsafe-candidate"): (3, 3, 3, 3, 2, 2),
    ("dev", "fixed-candidate"): (3, 3, 3, 3, 2, 2),
    ("acceptance", "baseline"): (8, 7, 6, 5, 3, 3),
    ("acceptance", "unsafe-candidate"): (8, 8, 8, 7, 3, 2),
    ("acceptance", "fixed-candidate"): (8, 8, 8, 8, 3, 3),
}


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON evidence key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("Nonfinite JSON evidence")


def decode(raw):
    result = json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
    )
    canonical(result)  # Includes exponent overflow and lone surrogate rejection.
    return result


def same(actual, expected):
    assert canonical(actual) == canonical(expected), "Frozen regression evidence differs"


def assets(package):
    values = {}
    for name, expected in ASSET_PINS.items():
        raw = (package / name).read_bytes()
        assert digest(raw) == expected, "Frozen asset bytes differ"
        values[name] = decode(raw)
    sources = values["corpus.json"]
    cases = values["cases.json"]["cases"]
    profiles = values["profiles.json"]["profiles"]
    same([source["id"] for source in sources], list(BODY_SHA))
    same([case["id"] for case in cases], list(MANUAL_CODES))
    same([profile["id"] for profile in profiles], list(PROFILES))
    for source in sources:
        assert digest(source["body"].encode("utf-8")) == BODY_SHA[source["id"]]
    for profile in profiles:
        same([item["case_id"] for item in profile["outputs"]], list(MANUAL_CODES))
    return values


def layer_result(code):
    failed_layer = {
        "invalid_json": "parse",
        "invalid_schema": "schema",
        "invalid_quote": "evidence",
        "unread_citation": "evidence",
        "duplicate_citation": "evidence",
        "missing_required_citation": "evidence",
    }.get(code)
    result = {"passed": code is None}
    after = False
    for name in ("parse", "schema", "evidence"):
        status = "skipped" if after else "passed"
        layer_code = None
        if name == failed_layer:
            status, layer_code, after = "failed", code, True
        result[name] = {"status": status, "code": layer_code}
    return result


def manual_result(case_id, profile):
    return layer_result(MANUAL_CODES[case_id][PROFILES.index(profile)])


def summary(rows, profile_key):
    count = len(rows)
    result = {
        "case_count": count,
        "critical_count": sum(row["critical"] for row in rows),
        "critical_passed": sum(
            row["critical"] and row[profile_key]["evidence"]["status"] == "passed" for row in rows
        ),
    }
    for layer in ("parse", "schema", "evidence"):
        passed = sum(row[profile_key][layer]["status"] == "passed" for row in rows)
        result[layer + "_passed"] = passed
        result[layer + "_pass_rate"] = passed / count
    return result


def common():
    return {
        "experiment_version": VERSION,
        "measurement_mode": "synthetic-output-replay",
        "read_only": True,
        "model_calls": 0,
        "corpus_revision": "agent-corpus-v1",
        "corpus_sha256": ASSET_PINS["corpus.json"],
        "dataset_version": "output-regression-cases-v1",
        "dataset_sha256": ASSET_PINS["cases.json"],
        "profiles_version": "synthetic-outputs-v1",
        "profiles_sha256": ASSET_PINS["profiles.json"],
        "gate_version": "critical-no-regression-v1",
    }


def compare_report(cases, split, candidate):
    rows = [
        {
            "id": case["id"],
            "prompt": case["prompt"],
            "critical": case["critical"],
            "fixed_context": {
                key: case[key] for key in ("found_ids", "read_ids", "required_citation_ids")
            },
            "loaded_source_receipts": [
                {"document_id": source_id, "body_sha256": BODY_SHA[source_id]}
                for source_id in case["read_ids"]
            ],
            "baseline": manual_result(case["id"], "baseline"),
            "candidate": manual_result(case["id"], candidate),
        }
        for case in cases
        if case["split"] == split
    ]
    baseline_summary, candidate_summary = summary(rows, "baseline"), summary(rows, "candidate")
    count_keys = (
        "case_count",
        "parse_passed",
        "schema_passed",
        "evidence_passed",
        "critical_count",
        "critical_passed",
    )
    for name, value in (("baseline", baseline_summary), (candidate, candidate_summary)):
        assert tuple(value[key] for key in count_keys) == MANUAL_COUNTS[(split, name)]
    failed_ids = [
        row["id"]
        for row in rows
        if row["critical"] and row["candidate"]["evidence"]["status"] != "passed"
    ]
    failed_rules = ["critical_case_failed"] if failed_ids else []
    if candidate_summary["evidence_passed"] < baseline_summary["evidence_passed"]:
        failed_rules.append("pass_count_regression")
    return {
        **common(),
        "command": "compare",
        "split": split,
        "baseline": {"profile": "baseline", "revision": "v1", "summary": baseline_summary},
        "candidate": {"profile": candidate, "revision": "v1", "summary": candidate_summary},
        "cases": rows,
        "gate": {
            "passed": not failed_rules,
            "failed_rules": failed_rules,
            "failed_critical_case_ids": failed_ids,
        },
    }


def explain_report(values, case, profile):
    sources = {source["id"]: source for source in values["corpus.json"]}
    fixed_profile = next(
        item for item in values["profiles.json"]["profiles"] if item["id"] == profile
    )
    raw_output = next(
        item["raw_output"] for item in fixed_profile["outputs"] if item["case_id"] == case["id"]
    )
    return {
        **common(),
        "command": "explain",
        "profile": profile,
        "profile_revision": "v1",
        "case": case,
        "loaded_sources": [sources[source_id] for source_id in case["read_ids"]],
        "raw_output": raw_output,
        "result": manual_result(case["id"], profile),
    }


class Child:
    """A complete JSON response is insufficient until the actual child exits."""

    def __init__(self, args, cwd, env, *, timeout=8, grace=0.3):
        self.process = subprocess.Popen(  # noqa: S603 - fixed bundled CLI or owned self-test
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.deadline = time.monotonic() + timeout
        self.grace = grace
        self.selector = selectors.DefaultSelector()
        for name in ("stdout", "stderr"):
            self.selector.register(getattr(self.process, name), selectors.EVENT_READ, name)
        self.output = {"stdout": bytearray(), "stderr": bytearray()}
        self.reaped = False

    def finish(self):
        while self.selector.get_map():
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Regression CLI exceeded its bound")
            for key, _mask in self.selector.select(min(remaining, 0.05)):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    self.selector.unregister(key.fileobj)
                else:
                    self.output[key.data].extend(data)
                    assert sum(map(len, self.output.values())) <= MAX_OUTPUT, (
                        "CLI output exceeded its bound"
                    )
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Regression CLI exceeded its bound")
        code = self.process.wait(timeout=remaining)
        self.reaped = True
        return code, bytes(self.output["stdout"]), bytes(self.output["stderr"])

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        try:
            if self.process.poll() is None:
                stop_owned_process_group(
                    self.process, grace_seconds=self.grace, kill_grace_seconds=2
                )
            else:
                self.process.wait(timeout=1)
            self.reaped = True
        finally:
            self.selector.close()
            self.process.stdout.close()
            self.process.stderr.close()


def report(child, *, exit_code=0, error=None, help_output=False):
    code, stdout, stderr = child.finish()
    assert code == exit_code, "CLI exit status differs"
    assert not stderr, "CLI emitted unexpected diagnostics"
    assert SENTINEL.encode() not in stdout, "CLI reflected rejected input"
    if help_output:
        assert b"app.py" in stdout and stdout.strip() and not stdout.startswith(b"{")
        return stdout
    assert stdout.endswith(b"\n") and stdout.count(b"\n") == 1, "CLI requires one JSON line"
    result = decode(stdout)
    assert canonical(result) + b"\n" == stdout, "CLI output is not canonical"
    if error:
        same(result, {"experiment_version": VERSION, "error": {"code": error}})
    return result


class Lab:
    def __init__(self, python, package, env, cwd):
        self.python, self.package, self.env, self.cwd = python, package, env, cwd
        self.children, self.scenarios = [], []
        self.probe_count = 0
        self.deadline = time.monotonic() + 180

    def call(
        self, *args, exit_code=0, error=None, package=None, cwd=None, seed="1", help_output=False
    ):
        assert sum(len(arg) for arg in args) <= 8192, "Verifier input exceeded its bound"
        remaining = self.deadline - time.monotonic()
        assert remaining > 0, "Regression verification exceeded its total runtime bound"
        child = Child(
            [str(self.python), "-s", "-B", str((package or self.package) / "app.py"), *args],
            cwd or self.cwd,
            {**self.env, "PYTHONHASHSEED": seed},
            timeout=min(8, remaining),
        )
        self.children.append(child)
        with child:
            result = report(child, exit_code=exit_code, error=error, help_output=help_output)
        return result, bytes(child.output["stdout"])

    def clean(self):
        for child in self.children:
            assert child.reaped and child.process.returncode is not None, (
                "Owned child was not reaped"
            )
            try:
                os.kill(child.process.pid, 0)
            except ProcessLookupError:
                continue
            raise AssertionError("Owned child still exists")


def successful_cases(lab, values):
    cases = values["cases.json"]["cases"]
    for split in ("dev", "acceptance"):
        for profile in PROFILES:
            expected = compare_report(cases, split, profile)
            code = 0 if expected["gate"]["passed"] else 2
            actual, raw = lab.call(
                "compare", "--candidate", profile, "--split", split, exit_code=code
            )
            same(actual, expected)
            # Recount the actual raw report too: skipped layers cannot shrink denominators.
            for role in ("baseline", "candidate"):
                same(actual[role]["summary"], summary(actual["cases"], role))
            _again, repeated = lab.call(
                "compare",
                "--split",
                split,
                "--candidate",
                profile,
                exit_code=code,
                cwd=lab.package,
                seed="731",
            )
            assert raw == repeated, "Comparison changed with cwd/hash seed/flag order"
            lab.scenarios.append(f"compare-{split}-{profile}")
    for profile in PROFILES:
        for case in cases:
            actual, raw = lab.call("explain", "--profile", profile, "--case", case["id"])
            same(actual, explain_report(values, case, profile))
            if case["id"] in {"accept-found-unread", "accept-unicode"}:
                _again, repeated = lab.call(
                    "explain",
                    "--case",
                    case["id"],
                    "--profile",
                    profile,
                    cwd=lab.package,
                    seed="0",
                )
                assert raw == repeated, "Explanation changed with cwd/hash seed/flag order"
            lab.scenarios.append(f"explain-{profile}-{case['id']}")


def rejected_inputs(lab):
    for args in (("--help",), ("compare", "--help"), ("explain", "--help")):
        lab.call(*args, help_output=True)
    compare = ("compare", "--candidate", "baseline", "--split", "acceptance")
    explain = ("explain", "--profile", "baseline", "--case", "dev-api")
    invalid = [
        (),
        ("unknown", SENTINEL),
        ("compare",),
        ("explain",),
        (*compare, "--help"),
        (*explain, "--help"),
        ("--help", SENTINEL),
        ("compare", "--help", "--candidate", "baseline"),
        ("explain", "--help", "--case", "dev-api"),
        (*compare, "--candidate", "baseline"),
        (*compare, "--split", "dev"),
        (*explain, "--case", "dev-api"),
        (*explain, "--profile", "baseline"),
        ("compare", "--candidate=baseline", "--split", "dev"),
        ("explain", "--profile", "baseline", "--case=dev-api"),
        ("compare", "--candidate", SENTINEL, "--split", "dev"),
        ("compare", "--candidate", "baseline", "--split", SENTINEL),
        ("explain", "--profile", SENTINEL, "--case", "dev-api"),
        ("explain", "--profile", "baseline", "--case", SENTINEL),
        (*compare, SENTINEL),
        (*explain, "--unknown", SENTINEL),
    ]
    for args in invalid:
        lab.call(*args, exit_code=1, error="invalid_input")
    lab.scenarios.append("exact-help-and-cli-lexical-shapes")


def write_json(path, value):
    path.write_bytes(canonical(value) + b"\n")


def rejected_assets(lab, root, original):
    sequence = 0

    def copied():
        nonlocal sequence
        sequence += 1
        folder = root / f"mutated-package-{sequence}"
        shutil.copytree(
            lab.package,
            folder,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache"),
        )
        return folder

    def snapshot(folder):
        return {
            name: digest((folder / name).read_bytes()) if (folder / name).is_file() else None
            for name in ASSET_PINS
        }

    def rejected(folder, error):
        before = snapshot(folder)
        for args in (
            ("compare", "--candidate", "fixed-candidate", "--split", "acceptance"),
            ("explain", "--profile", "baseline", "--case", "dev-api"),
        ):
            lab.call(*args, package=folder, exit_code=1, error=error)
        same(snapshot(folder), before)

    invalid = {
        "invalid-utf8": b'"' + SENTINEL.encode() + b'\x80"',
        "truncated": b'{"' + SENTINEL.encode() + b'":',
        "nested-duplicate-key": ('{"a":{"x":"' + SENTINEL + '","x":0}}').encode(),
        "overflow": ('{"' + SENTINEL + '":1e999}').encode(),
        "nonfinite": ('{"' + SENTINEL + '":NaN}').encode(),
        "surrogate-value": ('{"' + SENTINEL + '":"\\ud800"}').encode(),
        "surrogate-key": ('{"\\udfff":"' + SENTINEL + '"}').encode(),
        "depth": b"[" * 34 + canonical(SENTINEL) + b"]" * 34,
        "oversize": b" " * 2000001,
    }
    for name in ASSET_PINS:
        for label, raw in invalid.items():
            folder = copied()
            (folder / name).write_bytes(raw)
            rejected(folder, "assets_invalid")
            lab.scenarios.append(f"{name}-{label}")
        folder = copied()
        (folder / name).unlink()
        rejected(folder, "assets_unavailable")
        lab.call("compare", "--help", SENTINEL, package=folder, exit_code=1, error="invalid_input")
        lab.scenarios.append(f"{name}-missing-and-cli-priority")
        folder = copied()
        # Equivalent JSON still changes the frozen original bytes.
        (folder / name).write_bytes((folder / name).read_bytes() + b" ")
        rejected(folder, "assets_invalid")
        lab.scenarios.append(f"{name}-raw-pin-drift")

    def altered(label, file_name, mutate, error="assets_invalid"):
        value = copy.deepcopy(original[file_name])
        mutate(value)
        folder = copied()
        write_json(folder / file_name, value)
        rejected(folder, error)
        lab.scenarios.append(label)

    altered("corpus-body-pin-drift", "corpus.json", lambda x: x[0].update(body=SENTINEL))
    altered("duplicate-source", "corpus.json", lambda x: x[1].update(id=x[0]["id"]))
    altered(
        "cross-split-case-duplicate", "cases.json", lambda x: x["cases"][3].update(id="dev-api")
    )
    altered("read-not-found", "cases.json", lambda x: x["cases"][1].update(read_ids=["api"]))
    altered(
        "required-not-read",
        "cases.json",
        lambda x: x["cases"][1].update(required_citation_ids=["api"]),
    )
    altered("duplicate-read", "cases.json", lambda x: x["cases"][0].update(read_ids=["api", "api"]))
    altered("critical-not-bool", "cases.json", lambda x: x["cases"][0].update(critical=1))
    altered("missing-case", "cases.json", lambda x: x["cases"].pop())
    altered("missing-profile-output", "profiles.json", lambda x: x["profiles"][1]["outputs"].pop())
    altered(
        "duplicate-profile-output",
        "profiles.json",
        lambda x: x["profiles"][1]["outputs"][1].update(case_id="dev-api"),
    )
    altered(
        "extra-profile-output",
        "profiles.json",
        lambda x: x["profiles"][1]["outputs"].append({"case_id": SENTINEL, "raw_output": "{}"}),
    )
    altered("output-order", "profiles.json", lambda x: x["profiles"][1]["outputs"].reverse())
    altered(
        "changed-inner-output-pin",
        "profiles.json",
        lambda x: x["profiles"][1]["outputs"][0].update(raw_output=SENTINEL),
    )
    for file_name, key in (
        ("cases.json", "dataset_version"),
        ("cases.json", "corpus_revision"),
        ("profiles.json", "profiles_version"),
        ("profiles.json", "measurement_mode"),
    ):
        altered(
            f"version-{key}",
            file_name,
            lambda x, field=key: x.update({field: SENTINEL, "extra": SENTINEL}),
            "incompatible_version",
        )
        altered(f"version-type-{key}", file_name, lambda x, field=key: x.update({field: 1}))
    altered(
        "unselected-profile-revision",
        "profiles.json",
        lambda x: x["profiles"][1].update(revision=SENTINEL),
        "incompatible_version",
    )


# Fixed function probes supplement (and do not bypass) public pinned CLI checks.
# The product function is the SUT. All expected layer results below are manual.
RULE_PROBE = r"""
import copy
import json
import sys
from pathlib import Path

package = Path(sys.argv[1])
sys.path.insert(0, str(package))
from evaluator import evaluate_output

documents = json.loads((package / "corpus.json").read_text())
cases = {item["id"]: item for item in json.loads((package / "cases.json").read_text())["cases"]}
profiles = json.loads((package / "profiles.json").read_text())["profiles"]
unsafe = next(item for item in profiles if item["id"] == "unsafe-candidate")
raw_unread = next(item["raw_output"] for item in unsafe["outputs"] if item["case_id"] == "accept-found-unread")
results = {}

def run(name, raw, case_id="accept-api", changed=None):
    case = copy.deepcopy(cases[case_id])
    if changed:
        case.update(changed)
    results[name] = evaluate_output(raw, case, copy.deepcopy(documents))

def raw(answer="机械检查", citations=None, **extra):
    return json.dumps({"answer": answer, "citations": citations or [], **extra}, ensure_ascii=False)

api = {"document_id": "api", "quote": "API 明确输入长度、错误类别和超时预算。"}
billing = {"document_id": "billing-a", "quote": "练习声明 A：取消后一定不计费。"}
tools = {"document_id": "tools", "quote": "工具参数由服务端独立校验。"}
bad_quote = {"document_id": "api", "quote": "原文不存在的连续摘录"}
unread = {"document_id": "tools", "quote": "无关内容"}
run("fixed-unread", raw_unread, "accept-found-unread")
run("now-read", raw_unread, "accept-found-unread", {"read_ids": ["api"], "required_citation_ids": ["api"]})
run("changed-quote", raw(citations=[bad_quote]))
run("missing-conflict-side", raw(citations=[billing]), "accept-conflict")
run("duplicate-before-unread", raw(citations=[unread, unread]))
run("quote-before-later-unread", raw(citations=[bad_quote, unread]))
run("unread-before-later-quote", raw(citations=[unread, bad_quote]))
run("blank-quote", raw(citations=[{"document_id": "api", "quote": " \t "}]))
run("additional-read-citation", raw(citations=[api, tools]), changed={"found_ids": ["api", "tools"], "read_ids": ["api", "tools"]})
run("extra-schema-field", raw(citations=[api], extra=True))
run("numeric-answer", raw(answer=42, citations=[api]))
run("boolean-answer", raw(answer=True, citations=[api]))
run("blank-answer", raw(answer=" \n "), "accept-empty")
run("four-citations", raw(citations=[api] * 4))
run("answer-boundary", raw(answer="x" * 20000), "accept-empty")
run("answer-over-boundary", raw(answer="x" * 20001), "accept-empty")
run("duplicate-root-key", '{"answer":"ok","answer":"changed","citations":[]}', "accept-empty")
run("duplicate-nested-key", '{"answer":"ok","citations":[{"document_id":"api","document_id":"api","quote":"x"}]}')
run("overflow", '{"answer":1e999,"citations":[]}')
run("nonfinite", '{"answer":NaN,"citations":[]}')
run("lone-surrogate", '{"answer":"\\ud800","citations":[]}'.replace('\\\\ud800', '\\ud800'))
run("root-number", '42')
run("root-null", 'null')
run("root-array", '[]')
run("trailing-object", '{} {}')
run("depth-33", '[' * 33 + '0' + ']' * 33)
run("brackets-inside-string", raw(answer='[' * 64), "accept-empty")
run("raw-byte-over-limit", ' ' * 65537)
print(json.dumps(results, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
"""
RULE_EXPECTATIONS = {
    "fixed-unread": "unread_citation",
    "now-read": None,
    "changed-quote": "invalid_quote",
    "missing-conflict-side": "missing_required_citation",
    "duplicate-before-unread": "duplicate_citation",
    "quote-before-later-unread": "invalid_quote",
    "unread-before-later-quote": "unread_citation",
    "blank-quote": "invalid_quote",
    "additional-read-citation": None,
    "extra-schema-field": "invalid_schema",
    "numeric-answer": "invalid_schema",
    "boolean-answer": "invalid_schema",
    "blank-answer": "invalid_schema",
    "four-citations": "invalid_schema",
    "answer-boundary": None,
    "answer-over-boundary": "invalid_schema",
    "duplicate-root-key": "invalid_json",
    "duplicate-nested-key": "invalid_json",
    "overflow": "invalid_json",
    "nonfinite": "invalid_json",
    "lone-surrogate": "invalid_json",
    "root-number": "invalid_schema",
    "root-null": "invalid_schema",
    "root-array": "invalid_schema",
    "trailing-object": "invalid_json",
    "depth-33": "invalid_json",
    "brackets-inside-string": None,
    "raw-byte-over-limit": "invalid_json",
}


def rule_probes(lab):
    remaining = lab.deadline - time.monotonic()
    assert remaining > 0
    child = Child(
        [str(lab.python), "-s", "-B", "-c", RULE_PROBE, str(lab.package)],
        lab.cwd,
        {**lab.env, "PYTHONHASHSEED": "1"},
        timeout=min(8, remaining),
    )
    lab.children.append(child)
    lab.probe_count += 1
    with child:
        actual = report(child)
    same(actual, {name: layer_result(code) for name, code in RULE_EXPECTATIONS.items()})
    lab.scenarios.append("fixed-function-probes-with-independent-counterfactual-expectations")


def clean_environment(root):
    home = root / "home"
    home.mkdir()
    runtime = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"}
    install = {
        key: os.environ[key]
        for key in ("PATH", "UV_CACHE_DIR", "TMPDIR", "CI")
        if key in os.environ
    }
    return {**runtime, **install}, runtime


def owned_command(args, folder, env):
    process = subprocess.Popen(args, cwd=folder, env=env, start_new_session=True)  # noqa: S603 - fixed install/native commands
    try:
        assert process.wait(timeout=600) == 0, "Frozen regression command failed"
    finally:
        if process.poll() is None:
            stop_owned_process_group(process)
        else:
            process.wait(timeout=1)


def verify():
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required on PATH")
    temp_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(
        prefix="deep-ai-regression-verify-", dir=temp_root
    ) as temporary:
        root = Path(temporary).resolve()
        assert not root.is_relative_to(ROOT)
        package = root / "package"
        package.mkdir()
        with zipfile.ZipFile(ARCHIVE) as archive:
            assert sum(item.file_size for item in archive.infolist()) <= 4000000
            assert len({item.filename for item in archive.infolist()}) == len(archive.infolist())
            assert all(
                not item.is_dir() and (package / item.filename).resolve().is_relative_to(package)
                for item in archive.infolist()
            )
            archive.extractall(package)
        values = assets(package)
        install, runtime = clean_environment(root)
        owned_command([uv, "sync", "--locked", "--python", "3.12"], package, install)
        for command in (
            ("ruff", "check", "."),
            ("ruff", "format", "--check", "."),
            ("pytest", "-q"),
        ):
            owned_command([uv, "run", "--frozen", *command], package, install)
        cwd = root / "unrelated-cwd"
        cwd.mkdir()
        for name in ASSET_PINS:
            (cwd / name).write_text(SENTINEL)
        lab = Lab(package / ".venv/bin/python", package, runtime, cwd)
        try:
            successful_cases(lab, values)
            rule_probes(lab)
            rejected_inputs(lab)
            rejected_assets(lab, root, values)
        finally:
            lab.clean()
        result = {
            "lab": VERSION,
            "archive": "verified",
            "native_checks": "passed",
            "asset_sha256": ASSET_PINS,
            "source_body_sha256": BODY_SHA,
            "manual_profile_case_cells": 33,
            "compare_combinations": 6,
            "black_box_scenarios": len(lab.scenarios),
            "scenarios": lab.scenarios,
            "cli_processes_reaped": len(lab.children) - lab.probe_count,
            "rule_probe_processes_reaped": lab.probe_count,
            "rule_probe_cases": len(RULE_EXPECTATIONS),
            "model_calls": 0,
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    verify()
