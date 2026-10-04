"""Check teaching text-upload archives through actual HTTP and original bytes."""

import argparse
import base64
import http.client
import json
import socket

from build_course_labs import ROOT, UPLOAD_FILES
from verify_course_labs import verify
from verify_upload_storage import verify_storage


def request(base, case):
    prefix = "http://127.0.0.1:"
    if not base.startswith(prefix) or not base[len(prefix) :].isdigit():
        raise RuntimeError("Upload checks only permit a loopback port")
    body = None
    if "body_base64" in case:
        body = base64.b64decode(case["body_base64"], validate=True)
    elif "raw" in case:
        body = case["raw"].encode("utf-8")
    elif "repeat_body" in case:
        repeat = case["repeat_body"]
        body = (repeat["character"] * repeat["count"]).encode("utf-8")
    pairs = list(case.get("headers", {}).items()) + list(case.get("header_pairs", []))
    connection = http.client.HTTPConnection("127.0.0.1", int(base[len(prefix) :]), timeout=5)
    try:
        connection.putrequest(case["method"], case["path"])
        for name, value in pairs:
            connection.putheader(name, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.putheader("Connection", "close")
        connection.endheaders(body)
        response = connection.getresponse()
        if response.status != case["status"]:
            raise RuntimeError(f"{case['id']}: unexpected HTTP status {response.status}")
        payload = response.read(65537)
        if len(payload) > 65536:
            raise RuntimeError(f"{case['id']}: response exceeded the verifier budget")
        if "expected_body_base64" in case:
            if payload != base64.b64decode(case["expected_body_base64"], validate=True):
                raise RuntimeError(f"{case['id']}: downloaded bytes changed")
        else:
            if (response.getheader("Content-Type") or "").split(";", 1)[0] != "application/json":
                raise RuntimeError(f"{case['id']}: expected a JSON response")
            if json.dumps(json.loads(payload), sort_keys=True) != json.dumps(
                case["expected"], sort_keys=True
            ):
                raise RuntimeError(f"{case['id']}: JSON differs from the shared contract")
        for name, value in case.get("response_headers", {}).items():
            if response.getheader(name) != value:
                raise RuntimeError(f"{case['id']}: unexpected {name} response header")
        if response.getheader("Access-Control-Allow-Origin") is not None:
            raise RuntimeError(f"{case['id']}: private upload must not grant CORS")
        if response.getheader("Set-Cookie") is not None:
            raise RuntimeError(f"{case['id']}: bearer-only lab must not set cookies")
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=UPLOAD_FILES)
    args = parser.parse_args()
    cases = json.loads((ROOT / "labs/text-upload/shared/contract-cases.json").read_text())
    restart_cases = [cases[0]] + [
        {
            "id": f"restart-clears-{owner}",
            "method": "GET",
            "path": "/documents",
            "headers": {"Authorization": f"Bearer lab-{owner}-session"},
            "status": 200,
            "expected": {"documents": []},
            "response_headers": {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        }
        for owner in ("alice", "bob")
    ]
    for language in [args.language] if args.language else UPLOAD_FILES:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        verify(language, port, "text-upload", request, restart_cases, after_verify=verify_storage)


if __name__ == "__main__":
    main()
