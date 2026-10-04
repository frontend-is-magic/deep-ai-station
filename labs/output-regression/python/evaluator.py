"""Mechanical constraints over synthetic outputs, never a semantic or model-quality judge."""

from loader import OUTPUT_LIMIT, metadata, sha256, shape, strict_json


def layer(status: str, code: str | None = None) -> dict:
    return {"status": status, "code": code}


def valid_schema(value) -> bool:
    if not shape(value, {"answer", "citations"}):
        return False
    answer, citations = value["answer"], value["citations"]
    if not isinstance(answer, str) or not 1 <= len(answer) <= 20000 or not answer.strip():
        return False
    if not isinstance(citations, list) or len(citations) > 3:
        return False
    return all(
        shape(citation, {"document_id", "quote"})
        and isinstance(citation["document_id"], str)
        and 1 <= len(citation["document_id"]) <= 80
        and isinstance(citation["quote"], str)
        and 1 <= len(citation["quote"]) <= 1000
        for citation in citations
    )


def evidence_error(value: dict, case: dict, documents: list[dict]) -> str | None:
    ids = [citation["document_id"] for citation in value["citations"]]
    if len(set(ids)) != len(ids):
        return "duplicate_citation"
    # Resolving fixed read_ids in local data is not an observed model/tool read trace.
    reads = {
        document["id"]: document for document in documents if document["id"] in case["read_ids"]
    }
    for citation in value["citations"]:
        document = reads.get(citation["document_id"])
        if document is None:
            return "unread_citation"
        if not citation["quote"].strip() or citation["quote"] not in document["body"]:
            return "invalid_quote"
    if not set(case["required_citation_ids"]) <= set(ids):
        return "missing_required_citation"
    return None


def evaluate_output(raw_output: str, case: dict, documents: list[dict]) -> dict:
    result = {
        "parse": layer("skipped"),
        "schema": layer("skipped"),
        "evidence": layer("skipped"),
        "passed": False,
    }
    try:
        value = strict_json(raw_output, OUTPUT_LIMIT)
    except ValueError:
        result["parse"] = layer("failed", "invalid_json")
        return result
    result["parse"] = layer("passed")
    if not valid_schema(value):
        result["schema"] = layer("failed", "invalid_schema")
        return result
    result["schema"] = layer("passed")
    error = evidence_error(value, case, documents)
    result["evidence"] = layer("failed", error) if error else layer("passed")
    result["passed"] = error is None
    return result


def summarize(cases: list[dict], side: str) -> dict:
    count = len(cases)
    result = {"case_count": count}
    for name in ("parse", "schema", "evidence"):
        passed = sum(row[side][name]["status"] == "passed" for row in cases)
        result[f"{name}_passed"] = passed
        result[f"{name}_pass_rate"] = passed / count
    critical = [row for row in cases if row["critical"]]
    result["critical_count"] = len(critical)
    result["critical_passed"] = sum(row[side]["passed"] for row in critical)
    return result


def quality_gate(cases: list[dict]) -> dict:
    critical_failed = [
        row["id"] for row in cases if row["critical"] and not row["candidate"]["passed"]
    ]
    failures = []
    if critical_failed:
        failures.append("critical_case_failed")
    if sum(row["candidate"]["passed"] for row in cases) < sum(
        row["baseline"]["passed"] for row in cases
    ):
        failures.append("pass_count_regression")
    return {
        "passed": not failures,
        "failed_rules": failures,
        "failed_critical_case_ids": critical_failed,
    }


def compare(assets: tuple[list, dict, dict], candidate_id: str, split: str) -> dict:
    corpus, dataset, profiles = assets
    by_id = {document["id"]: document for document in corpus}
    profile_map = {profile["id"]: profile for profile in profiles["profiles"]}
    outputs = {
        name: {row["case_id"]: row["raw_output"] for row in profile_map[name]["outputs"]}
        for name in ("baseline", candidate_id)
    }
    cases = []
    for case in dataset["cases"]:
        if case["split"] != split:
            continue
        cases.append(
            {
                "id": case["id"],
                "prompt": case["prompt"],
                "critical": case["critical"],
                "fixed_context": {
                    key: list(case[key])
                    for key in ("found_ids", "read_ids", "required_citation_ids")
                },
                "loaded_source_receipts": [
                    {"document_id": key, "body_sha256": sha256(by_id[key]["body"].encode("utf-8"))}
                    for key in case["read_ids"]
                ],
                "baseline": evaluate_output(outputs["baseline"][case["id"]], case, corpus),
                "candidate": evaluate_output(outputs[candidate_id][case["id"]], case, corpus),
            }
        )
    return {
        **metadata(),
        "command": "compare",
        "split": split,
        "baseline": {
            "profile": "baseline",
            "revision": profile_map["baseline"]["revision"],
            "summary": summarize(cases, "baseline"),
        },
        "candidate": {
            "profile": candidate_id,
            "revision": profile_map[candidate_id]["revision"],
            "summary": summarize(cases, "candidate"),
        },
        "cases": cases,
        "gate": quality_gate(cases),
    }


def explain(assets: tuple[list, dict, dict], profile_id: str, case_id: str) -> dict:
    corpus, dataset, profiles = assets
    case = next(case for case in dataset["cases"] if case["id"] == case_id)
    profile = next(profile for profile in profiles["profiles"] if profile["id"] == profile_id)
    raw = next(
        output["raw_output"] for output in profile["outputs"] if output["case_id"] == case_id
    )
    documents = {document["id"]: document for document in corpus}
    return {
        **metadata(),
        "command": "explain",
        "profile": profile_id,
        "profile_revision": profile["revision"],
        "case": case,
        "loaded_sources": [documents[key] for key in case["read_ids"]],
        "raw_output": raw,
        "result": evaluate_output(raw, case, corpus),
    }
