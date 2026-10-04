"""Verify the fixed Agent loop ZIP against manually frozen snapshots and actual calls.

Expected observations, decisions and counts were assigned from the contract and
original teaching text before the implementation existed. No product import is
used to derive expected values. The only imports of package code occur inside a
fixed child process where the engine and policy are the system under test.
"""

# ruff: noqa: S101 - fixed maintainer-owned acceptance assertions

import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from verify_memory_lab import Child, assert_reaped, canonical, decode, owned_command, snapshot

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "public/labs/agent-loop-python.zip"
MEMBERS = {
    "agent_loop.py",
    "engine.py",
    "policies.py",
    "tools.py",
    "test_loop.py",
    "test_cli.py",
    "pyproject.toml",
    "uv.lock",
    "fixtures.json",
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "manifest.json",
    ".prettierrc.json",
}
VERSION = "agent-loop-v1"
LOOP_KEYS = (
    "max_steps",
    "decision_count",
    "tool_dispatch_count",
    "stop_reason",
    "steps",
    "final_state",
    "result",
)
# Literal original text and manually assigned transitions; not a second policy engine.
assets = {
    "asset_version": "agent-loop-corpus-v1",
    "documents": [
        {
            "id": "loop-guide",
            "title": "循环与停止",
            "body": "每轮先读取上一轮观察，再选择下一步动作。没有停止条件的循环可能反复调用同一工具。",
            "source": "https://developers.openai.com/api/docs/guides/agents",
            "kind": "teaching-summary",
            "keywords": ["循环", "停止"],
        },
        {
            "id": "tool-guide",
            "title": "只读工具与观察",
            "body": "搜索结果只提供资料标识。读取工具返回原文后，才拥有本次已读证据。",
            "source": "https://developers.openai.com/api/docs/guides/function-calling",
            "kind": "teaching-summary",
            "keywords": ["工具", "观察"],
        },
    ],
    "cases": [
        {"id": "found", "query": "循环与工具"},
        {"id": "empty", "query": "zzqxjx-agent-loop-empty-2048"},
    ],
}
asset_sha = hashlib.sha256(canonical(assets)).hexdigest()
assert asset_sha == "45a01bfcdff94b3d96826ba2f9eab7a750a51ea3e362939d8c2a0e1d1c06007d"
L = {k: assets["documents"][0][k] for k in ("id", "title", "body", "source", "kind")}
T = {k: assets["documents"][1][k] for k in ("id", "title", "body", "source", "kind")}
Q = "循环与工具"
Z = "zzqxjx-agent-loop-empty-2048"
F = {"type": "search_result", "query": Q, "found_ids": ["loop-guide", "tool-guide"]}
E = {"type": "search_result", "query": Z, "found_ids": []}
R = {"type": "read_result", "document": L}
A = {"query": Q, "found_ids": [], "read_documents": [], "last_observation": None}
B = {
    "query": Q,
    "found_ids": ["loop-guide", "tool-guide"],
    "read_documents": [],
    "last_observation": F,
}
C = {
    "query": Q,
    "found_ids": ["loop-guide", "tool-guide"],
    "read_documents": [L],
    "last_observation": R,
}
D = {"query": Z, "found_ids": [], "read_documents": [], "last_observation": None}
ES = {"query": Z, "found_ids": [], "read_documents": [], "last_observation": E}
SEARCH = {"action": "search", "query": Q}
EMPTY_SEARCH = {"action": "search", "query": Z}
READ = {"action": "read", "document_id": "loop-guide"}
DONE = {"action": "finish", "outcome": "completed"}
NONE = {"action": "finish", "outcome": "no_evidence"}


def step(index, before, decision, observation, after):
    return copy.deepcopy(
        {
            "index": index,
            "before": before,
            "decision": decision,
            "observation": observation,
            "after": after,
        }
    )


def report(
    case_id,
    policy,
    budget,
    decisions,
    dispatches,
    stop,
    steps,
    final,
    result,
    fingerprint=asset_sha,
):
    # Every row supplies its counts, stop, states and observations explicitly.
    return copy.deepcopy(
        {
            "contract_version": "agent-loop-v1",
            "ok": True,
            "lesson_id": "agent-agent-loop",
            "asset_version": "agent-loop-corpus-v1",
            "assets_sha256": fingerprint,
            "case_id": case_id,
            "policy": policy,
            "decision_source": "deterministic_policy",
            "read_only": True,
            "model_calls": 0,
            "max_steps": budget,
            "decision_count": decisions,
            "tool_dispatch_count": dispatches,
            "stop_reason": stop,
            "steps": steps,
            "final_state": final,
            "result": result,
        }
    )


def example(name, expected):
    return {
        "name": name,
        "argv": [
            "--case",
            expected["case_id"],
            "--policy",
            expected["policy"],
            "--max-steps",
            str(expected["max_steps"]),
        ],
        "exit_code": 0,
        "stderr": "",
        "report": expected,
    }


# Five independent assignments, not a reproduced decision loop.
rows = [
    example(
        "found-evidence-first-3",
        report(
            "found",
            "evidence_first",
            3,
            3,
            2,
            "completed",
            [
                step(1, A, SEARCH, F, B),
                step(2, B, READ, R, C),
                step(3, C, DONE, None, C),
            ],
            C,
            L,
        ),
    ),
    example(
        "found-evidence-first-1",
        report(
            "found",
            "evidence_first",
            1,
            1,
            1,
            "step_limit",
            [
                step(1, A, SEARCH, F, B),
            ],
            B,
            None,
        ),
    ),
    example(
        "empty-evidence-first-3",
        report(
            "empty",
            "evidence_first",
            3,
            2,
            1,
            "no_evidence",
            [
                step(1, D, EMPTY_SEARCH, E, ES),
                step(2, ES, NONE, None, ES),
            ],
            ES,
            None,
        ),
    ),
    example(
        "found-repeat-search-5",
        report(
            "found",
            "repeat_search",
            5,
            5,
            5,
            "step_limit",
            [
                step(1, A, SEARCH, F, B),
                step(2, B, SEARCH, F, B),
                step(3, B, SEARCH, F, B),
                step(4, B, SEARCH, F, B),
                step(5, B, SEARCH, F, B),
            ],
            B,
            None,
        ),
    ),
    example(
        "found-evidence-first-2",
        report(
            "found",
            "evidence_first",
            2,
            2,
            2,
            "step_limit",
            [
                step(1, A, SEARCH, F, B),
                step(2, B, READ, R, C),
            ],
            C,
            None,
        ),
    ),
]

EMPTY_FOUND = {"type": "search_result", "query": Q, "found_ids": []}
N = {"query": Q, "found_ids": [], "read_documents": [], "last_observation": EMPTY_FOUND}
empty_seam = {
    "query": Q,
    "max_steps": 3,
    "search_return": EMPTY_FOUND,
    "observed_calls": [{"tool": "search", "argument": Q}],
    "read_calls": 0,
    "loop_result": {
        "max_steps": 3,
        "decision_count": 2,
        "tool_dispatch_count": 1,
        "stop_reason": "no_evidence",
        "steps": [step(1, A, SEARCH, EMPTY_FOUND, N), step(2, N, NONE, None, N)],
        "final_state": N,
        "result": None,
    },
}

# One optional valid asset counterfactual proves the real search can choose and
# read the second original document. Expected trace is assigned here in advance.
changed = copy.deepcopy(assets)
changed["cases"][0]["query"] = "工具"
changed_sha = hashlib.sha256(canonical(changed)).hexdigest()
TA = {"query": "工具", "found_ids": [], "read_documents": [], "last_observation": None}
TF = {"type": "search_result", "query": "工具", "found_ids": ["tool-guide"]}
TB = {"query": "工具", "found_ids": ["tool-guide"], "read_documents": [], "last_observation": TF}
TR = {"type": "read_result", "document": T}
TC = {"query": "工具", "found_ids": ["tool-guide"], "read_documents": [T], "last_observation": TR}
second_document = example(
    "valid-found-query-tool",
    report(
        "found",
        "evidence_first",
        3,
        3,
        2,
        "completed",
        [
            step(1, TA, {"action": "search", "query": "工具"}, TF, TB),
            step(2, TB, {"action": "read", "document_id": "tool-guide"}, TR, TC),
            step(3, TC, DONE, None, TC),
        ],
        TC,
        T,
        changed_sha,
    ),
)
second_document["asset_change"] = {"cases[0].query": "工具"}

errors = {
    code: {
        "exit_code": exit_code,
        "stderr": "",
        "report": {
            "contract_version": "agent-loop-v1",
            "ok": False,
            "error": {"code": code, "message": message},
        },
    }
    for code, exit_code, message in [
        ("invalid_input", 2, "循环实验参数无效"),
        ("assets_invalid", 1, "循环实验资料无效"),
        ("execution_failed", 1, "循环实验未产生完整报告"),
    ]
}


def same(actual, expected):
    assert canonical(actual) == canonical(expected), "Agent loop differs from manual expectation"


def check_report(code, stdout, stderr, expected, exit_code=0):
    assert code == exit_code, "Agent loop exit status differs"
    assert stderr == b"", "Agent loop emitted diagnostics"
    assert stdout.endswith(b"\n") and stdout.count(b"\n") == 1, "Expected one UTF-8 JSON line"
    result = decode(stdout)
    same(result, expected)
    return result


def extract(package, archive_path=ARCHIVE):
    package.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        assert len(entries) == len(MEMBERS) and {entry.filename for entry in entries} == MEMBERS, (
            "Unexpected Agent loop ZIP members"
        )
        assert sum(entry.file_size for entry in entries) <= 2_000_000
        assert all(
            not entry.is_dir() and (entry.external_attr >> 16) & 0o170000 in (0, 0o100000)
            for entry in entries
        ), "ZIP members must be regular files"
        archive.extractall(package)
    same(
        decode((package / "manifest.json").read_bytes()),
        {
            "id": "agent-loop",
            "version": VERSION,
            "lessons": ["agent-agent-loop"],
            "languages": ["python"],
        },
    )
    value = decode((package / "fixtures.json").read_bytes())
    same(value, assets)
    assert hashlib.sha256(canonical(value)).hexdigest() == asset_sha


class Lab:
    """Reuse the existing bounded direct-child lifecycle; no listeners or browser."""

    def __init__(self, python, package, cwd, env):
        self.python, self.package, self.cwd, self.env = python, package, cwd, env
        self.children, self.scenarios = [], []
        self.deadline = time.monotonic() + 120

    def execute(self, argv, *, package=None, cwd=None, seed="17", probe=False):
        folder, working = package or self.package, cwd or self.cwd
        before, cwd_before = snapshot(folder), snapshot(working)
        remaining = self.deadline - time.monotonic()
        assert remaining > 0, "Agent loop verification exceeded its total deadline"
        command = [str(self.python), "-s", "-B"]
        command += ["-c", PROBE] if probe else ["-m", "agent_loop", *argv]
        child = Child(
            command,
            working,
            {
                **self.env,
                "PYTHONPATH": str(folder),
                "PYTHONHASHSEED": seed,
            },
            timeout=min(8, remaining),
        )
        self.children.append(child)
        with child:
            result = child.finish()
        assert_reaped(child)
        assert snapshot(folder) == before and snapshot(working) == cwd_before, (
            "Read-only Agent loop execution changed files"
        )
        return result

    def call(self, argv, expected, *, exit_code=0, **options):
        actual = self.execute(argv, **options)
        check_report(*actual, expected, exit_code)
        return actual[1]

    def clean(self):
        for child in self.children:
            assert_reaped(child)


def copy_package(package, root, name):
    folder = root / name
    folder.mkdir()
    for filename in MEMBERS:
        shutil.copyfile(package / filename, folder / filename)
    return folder


# Fixed executable probe: all expectations are checked by the parent against the
# manual oracle. The product engine/policy/tools are SUTs, never the oracle.
PROBE = """
import json
from engine import run_loop
from policies import decide_evidence_first
from tools import LocalTools, load_assets

query = '循环与工具'
empty_calls = []
empty_counts = {'decide': 0, 'read': 0}
def decide_empty(state):
    empty_counts['decide'] += 1
    return decide_evidence_first(state)
def search_empty(value):
    empty_calls.append({'tool': 'search', 'argument': value})
    return {'type': 'search_result', 'query': value, 'found_ids': []}
def read_forbidden(value):
    empty_counts['read'] += 1
    raise AssertionError('Read is forbidden after empty search')
empty = run_loop(query, decide_empty, 3, search=search_empty, read=read_forbidden)

local = LocalTools(load_assets())
budget_calls = []
budget_decides = 0
def decide_budget(state):
    global budget_decides
    budget_decides += 1
    return decide_evidence_first(state)
def search_budget(value):
    budget_calls.append({'tool': 'search', 'argument': value})
    return local.search(value)
def read_budget(value):
    budget_calls.append({'tool': 'read', 'argument': value})
    return local.read(value)
budget = run_loop(query, decide_budget, 2, search=search_budget, read=read_budget)
print(json.dumps({
    'empty_search': {'loop_result': empty, 'observed_calls': empty_calls,
                     'decision_calls': empty_counts['decide'], 'read_calls': empty_counts['read']},
    'budget_two': {'loop_result': budget, 'observed_calls': budget_calls, 'decision_calls': budget_decides},
}, ensure_ascii=False))
"""

BAD_POLICY = """

# Fixed verifier-only negative control: intentionally skip actual read.
def decide_evidence_first(snapshot):
    if snapshot['last_observation'] is None:
        return {'action': 'search', 'query': snapshot['query']}
    return {'action': 'finish', 'outcome': 'completed'}
"""


def probe_expected():
    return {
        "empty_search": {
            "loop_result": empty_seam["loop_result"],
            "observed_calls": [{"tool": "search", "argument": Q}],
            "decision_calls": 2,
            "read_calls": 0,
        },
        "budget_two": {
            "loop_result": {key: rows[4]["report"][key] for key in LOOP_KEYS},
            "observed_calls": [
                {"tool": "search", "argument": Q},
                {"tool": "read", "argument": "loop-guide"},
            ],
            "decision_calls": 2,
        },
    }


def assert_negative_control(actual):
    # Reuse the identical normal assertion, not a bad-policy marker. Verify the
    # precise public error too so syntax/import failures cannot count as detection.
    rejected = False
    try:
        check_report(*actual, rows[0]["report"])
    except AssertionError:
        rejected = True
    assert rejected, "Normal loop assertion accepted a policy that skipped read"
    check_report(*actual, errors["execution_failed"]["report"], 1)


def successful_cases(lab, root):
    original_stdout = None
    for row in rows:
        output = lab.call(row["argv"], row["report"])
        if original_stdout is None:
            original_stdout = output
    lab.scenarios.append("five-full-manual-traces-including-read-without-finish")

    different_cwd = root / "another-cwd"
    different_cwd.mkdir()
    (different_cwd / "fixtures.json").write_text("not-the-package-asset", encoding="utf-8")
    for working, seed in ((different_cwd, "73"), (lab.cwd, "193")):
        raw = lab.call(rows[0]["argv"], rows[0]["report"], cwd=working, seed=seed)
        assert raw == original_stdout, "Identical inputs changed stdout across cwd/hashseed"
    lab.scenarios.append("byte-identical-output-across-cwd-and-hashseed")

    changed_assets = copy.deepcopy(assets)
    changed_assets["cases"][0]["query"] = "工具"
    folder = copy_package(lab.package, root, "valid-query-counterfactual")
    (folder / "fixtures.json").write_bytes(canonical(changed_assets) + b"\n")
    assert hashlib.sha256(canonical(changed_assets)).hexdigest() == (
        "5797dc5bd4255438682f1ea8778f2a2a31caa53dcb7e6ef2ccdb5b47a3cbc205"
    )
    lab.call(second_document["argv"], second_document["report"], package=folder)
    lab.scenarios.append("changed-query-searches-and-reads-second-original-document")

    lab.call([], probe_expected(), probe=True)
    lab.scenarios.append("actual-empty-search-and-budget-two-call-spies")

    folder = copy_package(lab.package, root, "bad-policy")
    with (folder / "policies.py").open("a", encoding="utf-8") as handle:
        handle.write(BAD_POLICY)
    assert_negative_control(lab.execute(rows[0]["argv"], package=folder))
    lab.scenarios.append("same-normal-assertion-rejects-skip-read-policy")


def rejected_inputs_and_assets(lab, root):
    valid = rows[0]["argv"]
    for argv in (
        [*valid[:-1], "01"],
        [*valid, "--case", "found"],
        ["--help", *valid],
    ):
        lab.call(argv, errors["invalid_input"]["report"], exit_code=2)
    lab.scenarios.append("strict-budget-duplicate-flag-and-mixed-help")

    parent = root / "fallback-check"
    parent.mkdir()
    shared = parent / "shared"
    shared.mkdir()
    (shared / "fixtures.json").write_bytes(canonical(assets) + b"\n")
    shared_before = snapshot(shared)
    folder = copy_package(lab.package, parent, "python")
    (folder / "fixtures.json").write_bytes(b'{"documents":')
    lab.call(valid, errors["assets_invalid"]["report"], exit_code=1, package=folder)
    lab.call(["--help", *valid], errors["invalid_input"]["report"], exit_code=2, package=folder)
    (folder / "fixtures.json").unlink()
    (folder / "fixtures.json").mkdir()
    lab.call(valid, errors["assets_invalid"]["report"], exit_code=1, package=folder)
    assert snapshot(shared) == shared_before
    lab.scenarios.append("invalid-adjacent-resource-never-falls-back-and-cli-has-priority")


def verify():
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required on PATH")
    location = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(
        prefix="deep-ai-agent-loop-verify-", dir=location
    ) as temporary:
        root = Path(temporary).resolve()
        assert not root.is_relative_to(ROOT)
        package = root / "package"
        extract(package)
        original = snapshot(package)
        home = root / "home"
        home.mkdir()
        install = {
            key: os.environ[key]
            for key in ("PATH", "UV_CACHE_DIR", "TMPDIR", "CI")
            if key in os.environ
        }
        install.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
        owned_command([uv, "sync", "--frozen", "--python", "3.12"], package, install)
        for command in (
            ("ruff", "check", "--no-cache", "."),
            ("ruff", "format", "--check", "--no-cache", "."),
            ("pytest", "-q", "-p", "no:cacheprovider"),
        ):
            owned_command([uv, "run", "--frozen", *command], package, install)
        assert snapshot(package) == original, "Locked install/native checks changed packaged inputs"
        cwd = root / "unrelated-cwd"
        cwd.mkdir()
        (cwd / "fixtures.json").write_text("not-the-package-asset", encoding="utf-8")
        runtime = {
            "PATH": os.defpath,
            "HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        }
        lab = Lab(package / ".venv/bin/python", package, cwd, runtime)
        try:
            successful_cases(lab, root)
            rejected_inputs_and_assets(lab, root)
            assert snapshot(package) == original, "Original package changed during counterfactuals"
        finally:
            lab.clean()
        result = {
            "lab": VERSION,
            "archive_members": len(MEMBERS),
            "assets_sha256": asset_sha,
            "manual_baseline_reports": len(rows),
            "manual_baseline_steps": 13,
            "scenarios": lab.scenarios,
            "experiment_processes_reaped": len(lab.children),
            "native_checks": "passed",
            "source_and_assets_unchanged_by_cli": True,
            "skip_read_mutation_rejected_by_normal_assertion": True,
            "model_calls": 0,
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    verify()
