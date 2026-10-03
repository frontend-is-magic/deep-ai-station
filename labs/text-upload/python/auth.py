"""显式 Bearer 的公开假会话；仅作回环教学，不是生产登录。"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from starlette.requests import Request

from errors import LabError

Clock = Callable[[], float]
TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*", re.ASCII)
BEARER = re.compile(r"[Bb][Ee][Aa][Rr][Ee][Rr] +(.+)", re.ASCII)


@dataclass(frozen=True)
class Principal:
    user_id: str
    can_write: bool


@dataclass(frozen=True)
class Authentication:
    principal: Principal
    token: str


class SessionStore(Protocol):
    def resolve(self, token: str) -> Principal | None: ...


class MemorySessionStore:
    def __init__(self, sessions: list[dict], clock: Clock = time.monotonic):
        self.clock = clock
        created_at = clock()
        self._sessions = {
            value["token"]: {
                **value,
                "expires_at": created_at + value["expires_after_seconds"],
            }
            for value in sessions
        }
        self._lock = RLock()

    def resolve(self, token: str) -> Principal | None:
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session["revoked"] or self.clock() >= session["expires_at"]:
                return None
            return Principal(session["user_id"], session["can_write"])

    def revoke(self, token: str) -> None:
        with self._lock:
            if token in self._sessions:
                self._sessions[token]["revoked"] = True

    def set_write(self, token: str, allowed: bool) -> None:
        """仅启动/测试可信调用，无 HTTP 权限切换接口。"""
        with self._lock:
            if token in self._sessions:
                self._sessions[token]["can_write"] = allowed


def resolve_principal(store: SessionStore, token: str) -> Principal:
    try:
        principal = store.resolve(token)
    except Exception:
        raise LabError(503, "session_store_unavailable") from None
    if principal is None:
        raise LabError(401, "authentication_required")
    return principal


def authenticate(request: Request, store: SessionStore) -> Authentication:
    authorization = request.headers.getlist("authorization")
    cookies = [
        item
        for header in request.headers.getlist("cookie")
        for item in header.split(";")
        if item.partition("=")[0].strip(" \t") == "__Host-lab_session"
    ]
    if (
        len(authorization) > 1
        or any("," in value for value in authorization)
        or len(cookies) > 1
        or (authorization and cookies)
    ):
        raise LabError(400, "ambiguous_credentials")
    match = BEARER.fullmatch(authorization[0]) if authorization else None
    token = match[1] if match else ""
    if not 1 <= len(token) <= 128 or TOKEN.fullmatch(token) is None:
        raise LabError(401, "authentication_required")
    return Authentication(resolve_principal(store, token), token)


def require_write(principal: Principal) -> None:
    if not principal.can_write:
        raise LabError(403, "forbidden")
