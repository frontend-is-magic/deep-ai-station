"""Fixed lexical search and evidence-span metrics, without model calls."""

import re

from chunking import SPLITTER_VERSION, chunk_sources
from loader import LabError

SCORER_VERSION = "course-lexical-v1"
STOP = {"如何", "怎样", "什么", "一个", "应该", "可以", "需要", "进行", "以及"}
METRICS = ("evidence_recall_at_k", "chunk_precision_at_k", "first_evidence_reciprocal_rank")


def tokens(query: str) -> set[str]:
    result = set(re.findall(r"[a-z][a-z0-9_.-]+", query.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", query):
        result.add(phrase)
        result.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
    return set(sorted(result - STOP, key=lambda word: (-len(word), word))[:64])


def retrieve(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    if type(top_k) is not int or not 1 <= top_k <= 5:
        raise LabError("invalid_input")
    terms = tokens(query)
    source_order = {
        source_id: i for i, source_id in enumerate(dict.fromkeys(c["source_id"] for c in chunks))
    }
    results = []
    for chunk in chunks:
        matched = sorted(term for term in terms if term in chunk["text"].lower())
        if matched:
            results.append(
                {
                    **chunk,
                    "header_path": list(chunk["header_path"]),
                    "score": len(matched),
                    "matched_terms": matched,
                }
            )
    results.sort(
        key=lambda item: (
            -item["score"],
            source_order[item["source_id"]],
            item["start"],
            item["end"],
        )
    )
    return results[:top_k]


def covers(chunk: dict, gold: dict) -> bool:
    return (
        chunk["source_id"] == gold["source_id"]
        and chunk["source_revision"] == gold["source_revision"]
        and chunk["start"] <= gold["start"]
        and chunk["end"] >= gold["end"]
    )


def case_metrics(results: list[dict], gold: list[dict], top_k: int) -> dict:
    if not gold:
        return {**dict.fromkeys(METRICS), "no_result_accuracy": float(not results)}
    results = results[:top_k]
    covered = {item["gold_id"] for item in gold if any(covers(chunk, item) for chunk in results)}
    relevant = [any(covers(chunk, item) for item in gold) for chunk in results]
    first = next((index for index, hit in enumerate(relevant, 1) if hit), None)
    return {
        "evidence_recall_at_k": len(covered) / len(gold),
        "chunk_precision_at_k": sum(relevant) / top_k,
        "first_evidence_reciprocal_rank": 1 / first if first else 0.0,
        "no_result_accuracy": None,
    }


def evaluate(
    corpus: dict, dataset: dict, *, size: int = 80, overlap: int = 16, top_k: int = 2
) -> dict:
    if type(top_k) is not int or not 1 <= top_k <= 5:
        raise LabError("invalid_input")
    indexes = {
        "baseline": chunk_sources(corpus, "heading"),
        "candidate": chunk_sources(corpus, "window", size=size, overlap=overlap),
    }
    cases = []
    for case in dataset["cases"]:
        output = {**case, "gold": [dict(item) for item in case["gold"]]}
        for name, chunks in indexes.items():
            results = retrieve(case["query"], chunks, top_k)
            output[name] = {
                "results": results,
                "metrics": case_metrics(results, case["gold"], top_k),
                "retrieved_codepoints": sum(len(item["text"]) for item in results),
            }
        cases.append(output)
    summary = {}
    for name, chunks in indexes.items():
        positive = [case[name]["metrics"] for case in cases if case["gold"]]
        negative = [case[name]["metrics"] for case in cases if not case["gold"]]
        summary[name] = {
            "indexed_chunk_count": len(chunks),
            "indexed_codepoints": sum(len(item["text"]) for item in chunks),
            "retrieved_codepoints_total": sum(case[name]["retrieved_codepoints"] for case in cases),
            "positive_case_count": len(positive),
            "negative_case_count": len(negative),
            **{
                metric: sum(item[metric] for item in positive) / len(positive) if positive else None
                for metric in METRICS
            },
            "no_result_accuracy": sum(item["no_result_accuracy"] for item in negative)
            / len(negative)
            if negative
            else None,
        }
    return {
        "splitter_version": SPLITTER_VERSION,
        "scorer_version": SCORER_VERSION,
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_revision": dataset["dataset_revision"],
        "configurations": {
            "baseline": {"strategy": "heading", "configuration": {}},
            "candidate": {
                "strategy": "window",
                "configuration": {"size": size, "overlap": overlap},
            },
            "top_k": top_k,
        },
        "cases": cases,
        "summary": summary,
        "notice": "仅比较固定资料的词法检索，不代表模型回答质量或通用 RAG 质量。",
    }
