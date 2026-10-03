"""Verify fixed teaching-auth archives through real HTTP without real credentials."""

import argparse
import http.client
import json
import socket
from http.cookies import SimpleCookie

from build_course_labs import AUTH_FILES, ROOT
from verify_course_labs import verify


def request(base, case):
    """Keep duplicate headers intact; compare JSON values without bool/int coercion."""
    prefix = "http://127.0.0.1:"
    if not base.startswith(prefix) or not base[len(prefix) :].isdigit():
        raise RuntimeError("The maintainer verifier only permits a loopback port")
    body = None
    headers = list(case.get("headers", {}).items()) + list(case.get("header_pairs", []))
    if "json" in case:
        body = json.dumps(case["json"], ensure_ascii=False).encode()
        headers.append(("Content-Type", "application/json"))
    elif "raw" in case:
        body = case["raw"].encode()
    elif "repeat_body" in case:
        repeat = case["repeat_body"]
        body = (repeat["character"] * repeat["count"]).encode()
    if "content_type" in case:
        headers = [(key, value) for key, value in headers if key.lower() != "content-type"]
        headers.append(("Content-Type", case["content_type"]))
    connection = http.client.HTTPConnection("127.0.0.1", int(base[len(prefix) :]), timeout=5)
    try:
        connection.putrequest(case["method"], case["path"])
        for key, value in headers:
            connection.putheader(key, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.putheader("Connection", "close")
        connection.endheaders(body)
        response = connection.getresponse()
        if response.status != case["status"]:
            raise RuntimeError(f"{case['id']}: unexpected HTTP status {response.status}")
        if (response.getheader("Content-Type") or "").split(";", 1)[0] != "application/json":
            raise RuntimeError(f"{case['id']}: response must be JSON")
        payload = response.read(65537)
        if len(payload) > 65536:
            raise RuntimeError(f"{case['id']}: response too large")
        if json.dumps(json.loads(payload), sort_keys=True) != json.dumps(
            case["expected"], sort_keys=True
        ):
            raise RuntimeError(f"{case['id']}: response differs from the shared contract")
        for header, expected in case.get("response_headers", {}).items():
            if response.getheader(header) != expected:
                raise RuntimeError(f"{case['id']}: unexpected {header} response header")
        if response.getheader("Access-Control-Allow-Origin") is not None:
            raise RuntimeError(f"{case['id']}: teaching auth must not grant cross-origin reads")
        if "set_cookie" in case:
            raw = response.getheader("Set-Cookie")
            if not case["set_cookie"]:
                if raw:
                    raise RuntimeError(f"{case['id']}: bearer logout must not set a cookie")
            else:
                cookie = SimpleCookie()
                cookie.load(raw or "")
                if set(cookie) != {"__Host-lab_session"}:
                    raise RuntimeError(f"{case['id']}: missing session-cookie deletion")
                value = cookie["__Host-lab_session"]
                if (
                    value.value != ""
                    or value["path"] != "/"
                    or value["max-age"] != "0"
                    or value["domain"]
                    or not value["secure"]
                    or not value["httponly"]
                    or value["samesite"].lower() != "strict"
                ):
                    raise RuntimeError(f"{case['id']}: unsafe cookie deletion attributes")
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=AUTH_FILES)
    args = parser.parse_args()
    cases = json.loads((ROOT / "labs/session-authorization/shared/contract-cases.json").read_text())
    # Both sessions were revoked by the full sequence. A fresh teaching process
    # intentionally seeds them again; this is not durable production revocation.
    restart_cases = [cases[0]] + [
        {
            "id": f"restart-reseeds-{session}",
            "method": "GET",
            "path": "/me",
            "headers": {"Authorization": f"Bearer lab-{session}-session"},
            "status": 200,
            "expected": {"user_id": "alice"},
            "response_headers": {"Cache-Control": "no-store"},
        }
        for session in ("alice", "alice-readonly")
    ]
    for language in [args.language] if args.language else AUTH_FILES:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        verify(language, port, "session-authorization", request, restart_cases)


if __name__ == "__main__":
    main()
