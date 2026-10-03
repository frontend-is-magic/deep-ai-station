"""Bounded read-only CLI over bundled data; stdout is one canonical JSON result."""

import os
import re
import sys

from chunking import SPLITTER_VERSION, chunk_sources, configurations, quote_source
from loader import CONTRACT_VERSION, LabError, canonical, identifier, load_assets, revision
from retrieval import evaluate

COMMANDS = {
    "chunks": {"strategy", "size", "overlap"},
    "compare": {"size", "overlap", "top-k"},
    "quote": {"source-id", "revision", "start", "end"},
}
HELP = """Usage: python app.py <command> [options]
  chunks --strategy heading
  chunks --strategy window [--size 80] [--overlap 16]
  compare [--size 80] [--overlap 16] [--top-k 2]
  quote --source-id ID --revision sha256:HASH --start N --end N
Fixed local corpus only. Offsets are Unicode codepoints; no model or network calls.
"""


def integer(value: str) -> int:
    if not re.fullmatch(r"0|[1-9][0-9]{0,9}", value) or int(value) > 2147483647:
        raise LabError("invalid_input")
    return int(value)


def parse_arguments(argv: list[str]) -> tuple[str, dict]:
    if not argv or argv[0] not in COMMANDS or len(argv) > 20:
        raise LabError("invalid_input")
    command = argv[0]
    arguments = {}
    position = 1
    while position < len(argv):
        argument = argv[position]
        if not argument.startswith("--"):
            raise LabError("invalid_input")
        pair = argument[2:].split("=", 1)
        key = pair[0]
        if key not in COMMANDS[command] or key in arguments:
            raise LabError("invalid_input")
        if len(pair) == 2:
            value = pair[1]
        else:
            position += 1
            if position >= len(argv):
                raise LabError("invalid_input")
            value = argv[position]
        arguments[key] = value
        position += 1
    if command == "quote":
        if set(arguments) != COMMANDS[command]:
            raise LabError("invalid_input")
        if not identifier(arguments["source-id"]) or not revision(arguments["revision"]):
            raise LabError("invalid_input")
        arguments["start"] = integer(arguments["start"])
        arguments["end"] = integer(arguments["end"])
    else:
        strategy = arguments.get("strategy", "window")
        if command == "chunks" and (
            "strategy" not in arguments
            or (strategy == "heading" and any(key in arguments for key in ("size", "overlap")))
        ):
            raise LabError("invalid_input")
        arguments["size"] = integer(arguments.get("size", "80"))
        arguments["overlap"] = integer(arguments.get("overlap", "16"))
        configurations(strategy, arguments["size"], arguments["overlap"])
        if command == "compare":
            arguments["top-k"] = integer(arguments.get("top-k", "2"))
            if not 1 <= arguments["top-k"] <= 5:
                raise LabError("invalid_input")
    return command, arguments


def execute(argv: list[str]) -> dict:
    command, args = parse_arguments(argv)
    corpus, dataset = load_assets()
    result = {
        "contract_version": CONTRACT_VERSION,
        "ok": True,
        "command": command,
        "corpus_id": corpus["corpus_id"],
        "corpus_version": corpus["corpus_version"],
        "corpus_revision": corpus["corpus_revision"],
        "coordinate_unit": corpus["coordinate_unit"],
        "model_calls": 0,
    }
    if command == "chunks":
        chunks = chunk_sources(corpus, args["strategy"], size=args["size"], overlap=args["overlap"])
        result.update(
            splitter_version=SPLITTER_VERSION,
            strategy=args["strategy"],
            configuration=configurations(args["strategy"], args["size"], args["overlap"]),
            sources=corpus["sources"],
            chunks=chunks,
            indexed_chunk_count=len(chunks),
            indexed_codepoints=sum(len(item["text"]) for item in chunks),
        )
    elif command == "compare":
        result.update(
            evaluate(
                corpus, dataset, size=args["size"], overlap=args["overlap"], top_k=args["top-k"]
            )
        )
    else:
        result.update(
            quote_source(corpus, args["source-id"], args["revision"], args["start"], args["end"])
        )
    return result


def write_output(payload: bytes) -> bool:
    # Write without a buffered shutdown flush: a closed consumer gets no traceback/retry.
    remaining = memoryview(payload)
    try:
        while remaining:
            written = os.write(sys.stdout.fileno(), remaining)
            if written <= 0:
                return False
            remaining = remaining[written:]
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--help"] or (
        len(arguments) == 2 and arguments[0] in COMMANDS and arguments[1] == "--help"
    ):
        return 0 if write_output(HELP.encode("utf-8")) else 1
    try:
        payload = canonical(execute(arguments))
        status = 0
    except Exception as error:
        code = error.code if isinstance(error, LabError) else "experiment_failed"
        payload = canonical(
            {"contract_version": CONTRACT_VERSION, "ok": False, "error": {"code": code}}
        )
        status = 1
    return status if write_output(payload + b"\n") else 1


if __name__ == "__main__":
    raise SystemExit(main())
