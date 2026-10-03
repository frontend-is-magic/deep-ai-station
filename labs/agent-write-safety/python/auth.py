"""Public fixture sessions, deliberately not a production identity provider."""

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Protocol

from errors import LabError

Clock = Callable[[], float]
TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+={0,}", re.ASCII)
BEARER = re.compile(r"[Bb][Ee][Aa][Rr][Ee][Rr] +(.+)", re.ASCII)


def clock_value(clock: Clock) -> float:
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid clock")
    return value


@dataclass(frozen=True)
class Principal:
    owner_id: str
    requester_id: str
    capabilities: frozenset[str]


@dataclass(frozen=True)
class Authentication:
    principal: Principal
    token: str = field(repr=False)


class SessionStore(Protocol):
    def resolve(self, token: str) -> Principal | None: ...


class MemorySessionStore:
    def __init__(self, sessions: list[dict], clock: Clock = time.time):
        self.clock = clock
        created = clock_value(clock)
        self.sessions = {
            item["token"]: {
                **item,
                "capabilities": list(item["capabilities"]),
                "expires_at": created + item["expires_after_seconds"],
            }
            for item in sessions
        }
        self.lock = RLock()

    def resolve(self, token: str) -> Principal | None:
        with self.lock:
            item = self.sessions.get(token)
            if item is None or item["revoked"] or clock_value(self.clock) >= item["expires_at"]:
                return None
            return Principal(
                item["owner_id"], item["requester_id"], frozenset(item["capabilities"])
            )

    def revoke(self, token: str):
        with self.lock:
            if token in self.sessions:
                self.sessions[token]["revoked"] = True


def resolve(store: SessionStore, token: str) -> Principal:
    try:
        principal = store.resolve(token)
    except Exception:
        raise LabError(503, "session_store_unavailable") from None
    if principal is None:
        raise LabError(401, "authentication_required")
    return principal


def authenticate(request, store: SessionStore) -> Authentication:
    headers = request.headers.getlist("authorization")
    if len(headers) > 1 or any("," in value for value in headers):
        raise LabError(400, "ambiguous_credentials")
    match = BEARER.fullmatch(headers[0]) if headers else None
    token = match[1] if match else ""
    if not 1 <= len(token) <= 128 or TOKEN.fullmatch(token) is None:
        raise LabError(401, "authentication_required")
    return Authentication(resolve(store, token), token)


def refresh(auth: Authentication, store: SessionStore) -> Principal:
    principal = resolve(store, auth.token)
    if (principal.owner_id, principal.requester_id) != (
        auth.principal.owner_id,
        auth.principal.requester_id,
    ):
        raise LabError(401, "authentication_required")
    return principal


def require(principal: Principal, capability: str):
    if capability not in principal.capabilities:
        raise LabError(403, "forbidden")
