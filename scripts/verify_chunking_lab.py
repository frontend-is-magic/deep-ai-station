"""Verify the frozen chunking ZIP using bounded CLI processes and independent gold."""

# ruff: noqa: S101 - assertions specify the fixed maintainer-owned experiment

import copy
import hashlib
import json
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from verify_course_labs import stop_owned_process_group

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "public/labs/document-chunking-python.zip"
VERSION = "document-chunking-v1"
SPLITTER = "markdown-boundary-v1"
SCORER = "course-lexical-v1"
CORPUS_REVISION = "sha256:c1ef7914ddd34fdec4ed604c3dfb270c6ce19e431a10ccb9679837181bc79da5"
DATASET_REVISION = "sha256:a03d274140de23e046eb385b51717037d34f009d2a1d741c67ee34ab7e2db247"
ASSET_PINS = {
    "corpus.json": "sha256:67e12f61bc6954699784b5d28df09c1cabcc973230e3e42b58282e16e74a4d06",
    "cases.json": "sha256:3eed9ec3443603fca295bca70a5f7bb755c8bc22c1f496497754930837eb427b",
}
SOURCE_HASHES = {
    "api-guide": "sha256:c5e76dddbee38ce0619ec01e6ce41ffc94570bf423f3cb993c5431614a0e1f3e",
    "tool-evidence": "sha256:bf6a09d8e98c52294f205f7f8063fc7c6b11596cce3e70c9926ec9d9cc3cd5c7",
    "billing-conflict": "sha256:584a01d55dcdb7c8d9e42aa4d2da1cd891a829ae4b625f247fe972579e512077",
}
# Manually audited coordinates from the frozen source, not a second Markdown parser.
HEADINGS = {
    "api-guide": [
        (0, ["API 学习资料"]),
        (11, ["API 学习资料", "契约与超时"]),
        (85, ["API 学习资料", "记录"]),
    ],
    "tool-evidence": [
        (0, ["工具与证据"]),
        (9, ["工具与证据", "工具边界"]),
        (96, ["工具与证据", "引用"]),
        (163, ["工具与证据", "坐标样本"]),
        (205, ["工具与证据", "记录"]),
        (219, ["工具与证据", "记录"]),
    ],
    "billing-conflict": [
        (0, ["取消计费（合成资料）"]),
        (13, ["取消计费（合成资料）", "声明 A"]),
        (38, ["取消计费（合成资料）", "声明 B"]),
    ],
}
# Six independent spans, including both sides of the synthetic conflict.
GOLD = [
    ("api-budget", "api-guide", 20, 41, 38, 93, "API 明确输入长度、错误类别和超时预算。"),
    (
        "tool-write",
        "tool-evidence",
        31,
        81,
        75,
        197,
        "只读工具不能获得写入权限；需要写入时用 operation_id 去重，未知结果先回读，不盲目重试。",
    ),
    (
        "citation-boundary",
        "tool-evidence",
        103,
        144,
        249,
        372,
        "回答保留实际资料来源。没有匹配证据时明确说明，不构造来源或把模型猜测当成资料事实。",
    ),
    ("unicode-sequence", "tool-evidence", 172, 178, 442, 454, "A😀éZ。"),
    ("billing-side-a", "billing-conflict", 21, 37, 45, 89, "练习声明 A：取消后一定不计费。"),
    (
        "billing-side-b",
        "billing-conflict",
        46,
        78,
        102,
        194,
        "练习声明 B：取消只会终止传输，已经发生的供应商用量仍可能计费。",
    ),
]
QUERIES = [
    "API 超时 契约",
    "只读工具 operation_id 未知结果",
    "引用 资料 没有匹配证据",
    "坐标样本",
    "取消 计费",
    "zzzz unmatched",
]
METRICS = (
    "evidence_recall_at_k",
    "chunk_precision_at_k",
    "first_evidence_reciprocal_rank",
    "no_result_accuracy",
)
SENTINEL = "private-chunking-sentinel"
MAX_OUTPUT = 262144


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON evidence field")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("Nonfinite JSON evidence")


def decode(raw):
    result = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    canonical(result)  # Reject overflow floats and invalid Unicode, not only NaN literals.
    return result


def same(actual, expected):
    assert canonical(actual) == canonical(expected), "Fixed chunking evidence differs"


def assets(package):
    corpus, dataset = (decode((package / name).read_bytes()) for name in ASSET_PINS)
    for name, value in zip(ASSET_PINS, (corpus, dataset), strict=True):
        assert digest(canonical(value)) == ASSET_PINS[name], "Frozen asset differs"
    sources = corpus["sources"]
    same([source["source_id"] for source in sources], list(SOURCE_HASHES))
    for source, (codepoints, byte_count) in zip(
        sources, [(97, 249), (233, 551), (79, 195)], strict=True
    ):
        raw = source["text"].encode("utf-8")
        assert len(source["text"]) == codepoints and len(raw) == byte_count
        assert digest(raw) == source["source_revision"] == SOURCE_HASHES[source["source_id"]]
        assert source["source_uri"] == "course://chunking/" + source["source_id"]
    assert sources[1]["text"].count("\r\n") == 14
    by_id = {source["source_id"]: source for source in sources}
    expected_gold = []
    for gold_id, source_id, start, end, byte_start, byte_end, quote in GOLD:
        text = by_id[source_id]["text"]
        assert text[start:end] == quote
        assert len(text[:start].encode()) == byte_start
        assert len(text[:end].encode()) == byte_end
        expected_gold.append(
            {
                "gold_id": gold_id,
                "source_id": source_id,
                "source_revision": SOURCE_HASHES[source_id],
                "start": start,
                "end": end,
                "quote": quote,
            }
        )
    same([gold for case in dataset["cases"] for gold in case["gold"]], expected_gold)
    same([case["query"] for case in dataset["cases"]], QUERIES)
    assert [len(case["gold"]) for case in dataset["cases"]] == [1, 1, 1, 1, 2, 0]
    assert digest(canonical(dataset["cases"])) == DATASET_REVISION
    assert (
        digest(
            canonical(
                [
                    {key: source[key] for key in ("source_id", "source_uri", "source_revision")}
                    for source in sources
                ]
            )
        )
        == CORPUS_REVISION
    )
    return sources, dataset["cases"]


def common(command):
    return {
        "contract_version": VERSION,
        "ok": True,
        "command": command,
        "corpus_id": "agent-chunking-course-notes",
        "corpus_version": "chunking-corpus-v1",
        "corpus_revision": CORPUS_REVISION,
        "coordinate_unit": "unicode_codepoint",
        "model_calls": 0,
    }


def chunk(source, strategy, configuration, start, end):
    text = source["text"]
    identity = {
        "splitter_version": SPLITTER,
        "strategy": strategy,
        "configuration": configuration,
        "source_id": source["source_id"],
        "source_revision": source["source_revision"],
        "start": start,
        "end": end,
    }
    return {
        "chunk_id": digest(canonical(identity)),
        "source_id": source["source_id"],
        "source_uri": source["source_uri"],
        "source_revision": source["source_revision"],
        "start": start,
        "end": end,
        "byte_start": len(text[:start].encode()),
        "byte_end": len(text[:end].encode()),
        "header_path": [
            path for position, path in HEADINGS[source["source_id"]] if position <= start
        ][-1],
        "text": text[start:end],
    }


def chunks(sources, strategy, size=80, overlap=16):
    configuration = {} if strategy == "heading" else {"size": size, "overlap": overlap}
    result = []
    for source in sources:
        n = len(source["text"])
        if strategy == "heading":
            boundaries = [position for position, _path in HEADINGS[source["source_id"]]] + [n]
            ranges = list(zip(boundaries[:-1], boundaries[1:], strict=True))
        else:
            ranges = []
            for start in range(0, n, size - overlap):
                end = min(start + size, n)
                ranges.append((start, end))
                if end == n:
                    break
        result.extend(chunk(source, strategy, configuration, start, end) for start, end in ranges)
    return result


def chunks_report(sources, strategy, size=80, overlap=16):
    result = chunks(sources, strategy, size, overlap)
    return {
        **common("chunks"),
        "splitter_version": SPLITTER,
        "strategy": strategy,
        "configuration": {} if strategy == "heading" else {"size": size, "overlap": overlap},
        "sources": sources,
        "chunks": result,
        "indexed_chunk_count": len(result),
        "indexed_codepoints": sum(len(item["text"]) for item in result),
    }


def terms(query):
    stop = {"如何", "怎样", "什么", "一个", "应该", "可以", "需要", "进行", "以及"}
    result = set(re.findall(r"[a-z][a-z0-9_.-]+", query.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", query):
        result.add(phrase)
        result.update(phrase[index : index + 2] for index in range(len(phrase) - 1))
    return sorted(result - stop, key=lambda term: (-len(term), term))[:64]


def retrieve(index, query, top_k):
    result = []
    for item in index:
        matched = sorted(term for term in terms(query) if term in item["text"].lower())
        if matched:
            result.append({**item, "score": len(matched), "matched_terms": matched})
    order = {source_id: position for position, source_id in enumerate(SOURCE_HASHES)}
    return sorted(
        result,
        key=lambda item: (-item["score"], order[item["source_id"]], item["start"], item["end"]),
    )[:top_k]


def metrics(results, gold, top_k):
    coverage = [
        {
            item["gold_id"]
            for item in gold
            if item["source_id"] == result["source_id"]
            and item["source_revision"] == result["source_revision"]
            and result["start"] <= item["start"]
            and item["end"] <= result["end"]
        }
        for result in results
    ]
    if not gold:
        return dict(zip(METRICS, [None, None, None, 1.0 if not results else 0.0], strict=True))
    covered = set().union(*coverage)
    first = next((1.0 / rank for rank, ids in enumerate(coverage, 1) if ids), 0.0)
    return dict(
        zip(
            METRICS,
            [len(covered) / len(gold), sum(bool(ids) for ids in coverage) / top_k, first, None],
            strict=True,
        )
    )


def compare_report(sources, cases, size=80, overlap=16, top_k=2):
    indices = {
        "baseline": chunks(sources, "heading"),
        "candidate": chunks(sources, "window", size, overlap),
    }
    rows = []
    for case in cases:
        row = dict(case)
        for name, index in indices.items():
            found = retrieve(index, case["query"], top_k)
            row[name] = {
                "results": found,
                "metrics": metrics(found, case["gold"], top_k),
                "retrieved_codepoints": sum(len(item["text"]) for item in found),
            }
        rows.append(row)
    summary = {}
    for name, index in indices.items():
        summary[name] = {
            "indexed_chunk_count": len(index),
            "indexed_codepoints": sum(len(item["text"]) for item in index),
            "retrieved_codepoints_total": sum(row[name]["retrieved_codepoints"] for row in rows),
            "positive_case_count": 5,
            "negative_case_count": 1,
        }
        for key in METRICS:
            values = [
                row[name]["metrics"][key] for row in rows if row[name]["metrics"][key] is not None
            ]
            summary[name][key] = sum(values) / len(values)
    return {
        **common("compare"),
        "splitter_version": SPLITTER,
        "scorer_version": SCORER,
        "dataset_id": "chunking-evidence",
        "dataset_version": "chunking-evidence-v1",
        "dataset_revision": DATASET_REVISION,
        "configurations": {
            "baseline": {"strategy": "heading", "configuration": {}},
            "candidate": {
                "strategy": "window",
                "configuration": {"size": size, "overlap": overlap},
            },
            "top_k": top_k,
        },
        "cases": rows,
        "summary": summary,
        "notice": "仅比较固定资料的词法检索，不代表模型回答质量或通用 RAG 质量。",
    }


class Child:
    """Bound actual output and exit separately; closed pipes are not proof of exit."""

    def __init__(self, args, cwd, env, *, timeout=8, grace=0.3):
        self.process = subprocess.Popen(  # noqa: S603 - fixed package entry or self-test helper
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
                raise TimeoutError("Chunking CLI exceeded its bound")
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
            raise TimeoutError("Chunking CLI exceeded its bound")
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


def report(child, error=None, help_output=False):
    code, stdout, stderr = child.finish()
    assert code == (1 if error else 0), "CLI exit status differs"
    assert not stderr, "CLI emitted unexpected diagnostics"
    assert SENTINEL.encode() not in stdout, "CLI reflected rejected input"
    if help_output:
        assert b"app.py" in stdout and b"usage:" in stdout.lower() and not stdout.startswith(b"{")
        return stdout
    assert stdout.endswith(b"\n") and stdout.count(b"\n") == 1, "CLI requires one JSON line"
    result = decode(stdout)
    assert canonical(result) + b"\n" == stdout, "CLI output is not canonical"
    if error:
        same(result, {"contract_version": VERSION, "ok": False, "error": {"code": error}})
    return result


class Lab:
    def __init__(self, python, package, env, cwd):
        self.python, self.package, self.env, self.cwd = python, package, env, cwd
        self.children = []
        self.scenarios = []
        self.deadline = time.monotonic() + 120

    def call(self, *args, error=None, package=None, cwd=None, seed="1", help_output=False):
        assert sum(len(arg) for arg in args) <= 8192, "Verifier input exceeded its bound"
        remaining = self.deadline - time.monotonic()
        assert remaining > 0, "Chunking verification exceeded its total runtime bound"
        # No -E: the intentionally supplied PYTHONHASHSEED must actually take effect.
        child = Child(
            [str(self.python), "-s", "-B", str((package or self.package) / "app.py"), *args],
            cwd or self.cwd,
            {**self.env, "PYTHONHASHSEED": seed},
            timeout=min(8, remaining),
        )
        self.children.append(child)
        with child:
            result = report(child, error, help_output)
        return result, bytes(child.output["stdout"])

    def clean(self):
        for child in self.children:
            assert child.reaped and child.process.returncode is not None, "Owned CLI was not reaped"
            try:
                os.kill(child.process.pid, 0)
            except ProcessLookupError:
                continue
            raise AssertionError("Owned CLI still exists")


def successful_cases(lab, sources, cases):
    heading, heading_raw = lab.call("chunks", "--strategy", "heading")
    _again, repeated_raw = lab.call("chunks", "--strategy", "heading", cwd=lab.package, seed="731")
    assert repeated_raw == heading_raw, "Heading output changed with cwd/hash seed"
    same(heading, chunks_report(sources, "heading"))
    assert "".join(item["text"] for item in heading["chunks"]) == "".join(
        source["text"] for source in sources
    )
    repeated = [
        item
        for item in heading["chunks"]
        if item["source_id"] == "tool-evidence" and item["start"] >= 205
    ]
    assert (
        repeated[0]["text"] == repeated[1]["text"]
        and repeated[0]["chunk_id"] != repeated[1]["chunk_id"]
    )
    assert not any(item["start"] == 189 for item in heading["chunks"])
    lab.scenarios.append("heading-complete-partition-crlf-fence-repeated-section-identity")
    configurations = [
        (80, 16, 2),
        (32, 0, 1),
        (32, 16, 5),
        (34, 16, 5),
        (97, 0, 5),
        (256, 16, 5),
        (80, 16, 3),
    ]
    reports = []
    for size, overlap, top_k in configurations:
        result, _raw = lab.call(
            "chunks", "--strategy=window", f"--size={size}", "--overlap", str(overlap)
        )
        same(result, chunks_report(sources, "window", size, overlap))
        result, raw = lab.call(
            "compare", "--size", str(size), "--overlap", str(overlap), "--top-k", str(top_k)
        )
        same(result, compare_report(sources, cases, size, overlap, top_k))
        reports.append(result)
        if (size, overlap, top_k) == (80, 16, 2):
            default, default_raw = lab.call("compare")
            same(default, result)
            assert default_raw == raw
            for cwd, seed in ((lab.package, "0"), (lab.cwd, "731")):
                _again, repeated_raw = lab.call("compare", cwd=cwd, seed=seed)
                assert repeated_raw == raw, "CLI output changed with cwd/hash seed"
            default_window, _ = lab.call("chunks", "--strategy", "window")
            same(default_window, chunks_report(sources, "window"))
        lab.scenarios.append(f"independent-ranges-ranking-metrics-{size}-{overlap}-k{top_k}")
    assert reports[0]["cases"][1]["candidate"]["metrics"]["evidence_recall_at_k"] == 0.0
    assert reports[-1]["cases"][4]["baseline"]["metrics"]["evidence_recall_at_k"] == 0.5
    # Overlap can return two blocks that cover the same gold; recall must stay 1.
    duplicate_coverage = [
        row
        for result in reports
        for row in result["cases"]
        if len(row["gold"]) == 1
        and sum(
            bool(metrics([item], row["gold"], 1)["evidence_recall_at_k"])
            for item in row["candidate"]["results"]
        )
        > 1
    ]
    assert duplicate_coverage and all(
        row["candidate"]["metrics"]["evidence_recall_at_k"] == 1.0 for row in duplicate_coverage
    )
    lab.scenarios.append("denominators-deduplicated-gold-half-conflict-and-stable-bytes")
    by_id = {source["source_id"]: source for source in sources}
    for _gold_id, source_id, start, end, byte_start, byte_end, quote in GOLD:
        result, _raw = lab.call(
            "quote",
            "--source-id",
            source_id,
            "--revision",
            SOURCE_HASHES[source_id],
            "--start",
            str(start),
            "--end",
            str(end),
        )
        if source_id == "tool-evidence" and start == 172:
            _again, repeated_raw = lab.call(
                "quote",
                "--source-id",
                source_id,
                "--revision",
                SOURCE_HASHES[source_id],
                "--start",
                str(start),
                "--end",
                str(end),
                cwd=lab.package,
                seed="731",
            )
            assert repeated_raw == _raw, "Quote output changed with cwd/hash seed"
        same(
            result,
            {
                **common("quote"),
                "source_id": source_id,
                "source_uri": by_id[source_id]["source_uri"],
                "source_revision": SOURCE_HASHES[source_id],
                "start": start,
                "end": end,
                "byte_start": byte_start,
                "byte_end": byte_end,
                "quote": quote,
            },
        )
    lab.scenarios.append("six-gold-quotes-exact-codepoint-and-utf8-ranges")


def rejected_inputs(lab):
    quote = [
        "quote",
        "--source-id",
        "tool-evidence",
        "--revision",
        SOURCE_HASHES["tool-evidence"],
        "--start",
        "172",
        "--end",
        "178",
    ]
    bad = [
        [],
        [SENTINEL],
        ["-h"],
        ["compare", "--unknown", SENTINEL],
        ["compare", "--top", "2"],
        ["compare", "--top-k"],
        ["compare", "--top-k", "2", "--top-k=2"],
        ["chunks"],
        ["chunks", "--strategy", SENTINEL],
        ["chunks", "--strategy", "heading", "--size", "80"],
        ["chunks", "--strategy", "heading", "--overlap", "0"],
        ["quote", "--source-id", "../" + SENTINEL],
        ["compare", "--top-k", "0"],
        ["compare", "--top-k", "6"],
        ["compare", "--size", "31"],
        ["compare", "--size", "257"],
        ["compare", "--overlap", "17"],
        quote + ["--start", "172"],
    ]
    for value in ("-1", "+1", "01", " 1", "1\n", "1.0", "1e1", "2147483648", "10000000000"):
        bad.append(["compare", "--size", value])
    for args in bad:
        lab.call(*args, error="invalid_input")
    for args in (["--help"], ["chunks", "--help"], ["compare", "--help"], ["quote", "--help"]):
        lab.call(*args, help_output=True)
    for args in (
        ["--help", SENTINEL],
        ["--help", "--help"],
        ["compare", "--help", "--top-k", "2"],
        ["chunks", "--strategy", "heading", "--help"],
        [SENTINEL, "--help"],
        ["compare", "--help="],
        ["compare", "-h"],
    ):
        lab.call(*args, error="invalid_input")
    lab.scenarios.append("strict-cli-lexical-duplicates-ranges-and-help-whitelist")
    for index, value, error in (
        (2, SENTINEL, "source_not_found"),
        (4, "sha256:" + "0" * 64, "revision_mismatch"),
        (4, "sha256:" + "A" * 64, "invalid_input"),
        (6, "178", "invalid_range"),
        (8, "234", "invalid_range"),
        (8, "171", "invalid_range"),
        (8, "999", "invalid_range"),
    ):
        args = list(quote)
        args[index] = value
        lab.call(*args, error=error)
    lab.scenarios.append("quote-source-revision-shape-and-range-rejection")


def write_json(path, value):
    path.write_bytes(canonical(value) + b"\n")


def rehash_assets(corpus, dataset):
    for source in corpus["sources"]:
        source["source_revision"] = digest(source["text"].encode())
    corpus["corpus_revision"] = digest(
        canonical(
            [
                {key: source[key] for key in ("source_id", "source_uri", "source_revision")}
                for source in corpus["sources"]
            ]
        )
    )
    dataset["corpus_revision"] = corpus["corpus_revision"]
    sources = {source["source_id"]: source for source in corpus["sources"]}
    for case in dataset["cases"]:
        for gold in case["gold"]:
            source = sources[gold["source_id"]]
            gold["source_revision"] = source["source_revision"]
            gold["quote"] = source["text"][gold["start"] : gold["end"]]
    dataset["dataset_revision"] = digest(canonical(dataset["cases"]))


def rejected_assets(lab, root):
    original = {name: decode((lab.package / name).read_bytes()) for name in ASSET_PINS}
    counter = 0

    def copied():
        nonlocal counter
        counter += 1
        folder = root / f"mutated-{counter}"
        shutil.copytree(
            lab.package,
            folder,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache"),
        )
        return folder

    def rejected(folder, error):
        before = {
            name: digest((folder / name).read_bytes()) if (folder / name).exists() else None
            for name in ASSET_PINS
        }
        lab.call("compare", package=folder, error=error)
        after = {
            name: digest((folder / name).read_bytes()) if (folder / name).exists() else None
            for name in ASSET_PINS
        }
        same(before, after)

    # Both fixed asset readers must reject unsafe JSON before schema/pin validation.
    invalid = {
        "invalid-utf8": b'"' + SENTINEL.encode() + b'\x80"',
        "truncated-json": b'{"' + SENTINEL.encode() + b'":',
        "duplicate-key": ('{"x":"' + SENTINEL + '","x":0}').encode(),
        "overflow-number": ('{"' + SENTINEL + '":1e999}').encode(),
        "nan-number": ('{"' + SENTINEL + '":NaN}').encode(),
        "surrogate-value": ('{"' + SENTINEL + '":"\\ud800"}').encode(),
        "surrogate-key": ('{"\\udfff":"' + SENTINEL + '"}').encode(),
        "deep-json": b"[" * 34 + canonical(SENTINEL) + b"]" * 34,
        "oversize": b" " * 65537,
    }
    for name in ASSET_PINS:
        for label, raw in invalid.items():
            folder = copied()
            (folder / name).write_bytes(raw)
            rejected(folder, "corpus_invalid")
            lab.scenarios.append(f"{name}-{label}")
        folder = copied()
        (folder / name).unlink()
        rejected(folder, "corpus_invalid")
        lab.call("compare", "--help", "--bad", SENTINEL, package=folder, error="invalid_input")
        lab.scenarios.append(f"{name}-missing-and-cli-priority")
    for label, error in (
        ("text-with-stale-digest", "corpus_invalid"),
        ("source-digest", "corpus_invalid"),
        ("gold-offset", "corpus_invalid"),
        ("bool-coordinate", "corpus_invalid"),
        ("unsupported-version", "incompatible_version"),
        ("title-pin-drift", "incompatible_version"),
        ("consistent-source-revision-pin-drift", "incompatible_version"),
        ("consistent-dataset-pin-drift", "incompatible_version"),
    ):
        corpus, dataset = (
            copy.deepcopy(original["corpus.json"]),
            copy.deepcopy(original["cases.json"]),
        )
        if label == "text-with-stale-digest":
            corpus["sources"][0]["text"] += SENTINEL
        elif label == "source-digest":
            corpus["sources"][0]["source_revision"] = "sha256:" + "0" * 64
        elif label == "gold-offset":
            dataset["cases"][0]["gold"][0]["start"] += 1
        elif label == "bool-coordinate":
            dataset["cases"][0]["gold"][0]["start"] = True
        elif label == "unsupported-version":
            corpus["corpus_version"] = SENTINEL
        elif label == "title-pin-drift":
            corpus["sources"][0]["title"] = SENTINEL
        elif label == "consistent-source-revision-pin-drift":
            corpus["sources"][0]["text"] += SENTINEL
            rehash_assets(corpus, dataset)
        else:
            dataset["cases"][0]["question"] = SENTINEL
            rehash_assets(corpus, dataset)
        folder = copied()
        write_json(folder / "corpus.json", corpus)
        write_json(folder / "cases.json", dataset)
        rejected(folder, error)
        lab.scenarios.append(label)


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
    process = subprocess.Popen(args, cwd=folder, env=env, start_new_session=True)  # noqa: S603 - fixed frozen install/native commands
    try:
        assert process.wait(timeout=600) == 0, "Frozen chunking command failed"
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
    with tempfile.TemporaryDirectory(prefix="deep-ai-chunking-verify-", dir=temp_root) as temporary:
        root = Path(temporary).resolve()
        assert not root.is_relative_to(ROOT)
        package = root / "package"
        package.mkdir()
        with zipfile.ZipFile(ARCHIVE) as archive:
            assert sum(item.file_size for item in archive.infolist()) <= 1048576
            assert all(
                not item.is_dir() and (package / item.filename).resolve().is_relative_to(package)
                for item in archive.infolist()
            )
            archive.extractall(package)
        sources, cases = assets(package)
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
        # Conflicting cwd files must never become the package assets.
        (cwd / "corpus.json").write_text(SENTINEL)
        (cwd / "cases.json").write_text(SENTINEL)
        lab = Lab(package / ".venv/bin/python", package, runtime, cwd)
        successful_cases(lab, sources, cases)
        rejected_inputs(lab)
        rejected_assets(lab, root)
        lab.clean()
        result = {
            "lab": VERSION,
            "archive": "verified",
            "native_checks": "passed",
            "source_sha256": SOURCE_HASHES,
            "independent_gold_spans": len(GOLD),
            "black_box_scenarios": len(lab.scenarios),
            "scenarios": lab.scenarios,
            "cli_processes_reaped": len(lab.children),
            "model_calls": 0,
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    verify()
