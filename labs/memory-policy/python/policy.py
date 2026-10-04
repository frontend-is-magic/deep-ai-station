"""Pure qualification and resolution; knowledge is never interpreted as a command."""

from copy import deepcopy

from loader import CONTRACT_VERSION, LANGUAGES, LabError, asset_digest, canonical, shape

DECISIONS = ("eligible", "knowledge_only", "unconsented", "revoked", "future", "expired")
COMMON_KEYS = {
    "contract_version",
    "lesson_id",
    "model_calls",
    "read_only",
    "assets_sha256",
    "command",
}


def decision(record, case):
    if record["owner"] != case["owner"]:
        return "owner_mismatch"
    if record["scope"] != case["scope"]:
        return "scope_mismatch"
    if record["kind"] == "knowledge":
        return "knowledge_only"
    if not record["consent"]:
        return "unconsented"
    if record["revoked"]:
        return "revoked"
    if record["updated_at"] > case["as_of"]:
        return "future"
    if record["expires_at"] is not None and record["expires_at"] <= case["as_of"]:
        return "expired"
    return "eligible"


def resolve(records, request_language):
    languages = sorted(
        {item["memory"]["language"] for item in records if item["decision"] == "eligible"}
    )
    if request_language is not None:
        status, language, source = "resolved", request_language, "request"
    elif len(languages) == 1:
        status, language, source = "resolved", languages[0], "memory"
    else:
        status, language, source = "conflict" if languages else "no_memory", None, None
    return {
        "status": status,
        "language": language,
        "source": source,
        "candidate_languages": languages,
    }


def metadata(memories, cases, command):
    return {
        "contract_version": CONTRACT_VERSION,
        "lesson_id": "agent-memory",
        "model_calls": 0,
        "read_only": True,
        "assets_sha256": asset_digest(memories, cases),
        "command": command,
    }


def list_cases(memories, cases):
    return {
        **metadata(memories, cases, "list"),
        "cases": [
            {"id": case["id"], "title": case["title"]}
            for case in sorted(cases["cases"], key=lambda item: item["id"])
        ],
    }


def evaluate(memories, cases, case_id, request_language=None):
    if request_language is not None and (
        type(request_language) is not str or request_language not in LANGUAGES
    ):
        raise LabError("invalid_input")
    case = next((case for case in cases["cases"] if case["id"] == case_id), None)
    if case is None:
        raise LabError("invalid_input")
    titles = {source["id"]: source["title"] for source in memories["sources"]}
    selected = set(case["memory_ids"])
    records = []
    counts = {
        "selected": len(selected),
        "visible": 0,
        "owner_mismatch": 0,
        "scope_mismatch": 0,
        "eligible": 0,
    }
    for record in sorted(memories["memories"], key=lambda item: item["id"]):
        if record["id"] not in selected:
            continue
        category = decision(record, case)
        if category in ("owner_mismatch", "scope_mismatch"):
            counts[category] += 1
            continue
        counts["visible"] += 1
        counts["eligible"] += int(category == "eligible")
        records.append(
            {
                "memory": deepcopy(record),
                "source_title": titles[record["source"]],
                "decision": category,
            }
        )
    return {
        **metadata(memories, cases, "resolve"),
        "case_id": case_id,
        "as_of": case["as_of"],
        "context": {
            "owner": case["owner"],
            "scope": case["scope"],
            "request_language": request_language,
        },
        "counts": counts,
        "records": records,
        "resolution": resolve(records, request_language),
    }


def validate_report(report, memories, cases):
    """Validate exact public shape and source/ownership relations before any output."""

    def check(condition):
        if not condition:
            raise LabError("experiment_failed")

    check(type(report) is dict)
    command = report.get("command")
    check(command in ("list", "resolve"))
    extra = (
        {"cases"}
        if command == "list"
        else {"case_id", "as_of", "context", "counts", "records", "resolution"}
    )
    check(shape(report, COMMON_KEYS | extra))
    for key, value in metadata(memories, cases, command).items():
        check(type(report[key]) is type(value) and report[key] == value)
    if command == "list":
        check(report == list_cases(memories, cases))
        return
    case = next((item for item in cases["cases"] if item["id"] == report["case_id"]), None)
    check(case is not None and report["as_of"] == case["as_of"])
    context = report["context"]
    check(shape(context, {"owner", "scope", "request_language"}))
    check(context["owner"] == case["owner"] and context["scope"] == case["scope"])
    request = context["request_language"]
    check(request is None or (type(request) is str and request in LANGUAGES))
    counts = report["counts"]
    check(shape(counts, {"selected", "visible", "owner_mismatch", "scope_mismatch", "eligible"}))
    check(all(type(value) is int and 0 <= value <= 64 for value in counts.values()))
    records = report["records"]
    check(type(records) is list)
    selected = [item for item in memories["memories"] if item["id"] in case["memory_ids"]]
    visible = sorted(
        (
            item
            for item in selected
            if item["owner"] == case["owner"] and item["scope"] == case["scope"]
        ),
        key=lambda item: item["id"],
    )
    check(len(records) == len(visible) == counts["visible"])
    titles = {item["id"]: item["title"] for item in memories["sources"]}
    for item, original in zip(records, visible, strict=True):
        check(shape(item, {"memory", "source_title", "decision"}))
        # Canonical JSON equality also distinguishes true from 1 in nested data.
        check(canonical(item["memory"]) == canonical(original))
        check(item["source_title"] == titles[original["source"]])
        check(item["decision"] == decision(original, case))
    check(counts["selected"] == len(selected))
    check(counts["owner_mismatch"] == sum(item["owner"] != case["owner"] for item in selected))
    check(
        counts["scope_mismatch"]
        == sum(
            item["owner"] == case["owner"] and item["scope"] != case["scope"] for item in selected
        )
    )
    check(counts["eligible"] == sum(item["decision"] == "eligible" for item in records))
    check(report["resolution"] == resolve(records, request))
