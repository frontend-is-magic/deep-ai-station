"""公开假会话教学实现；不提供生产登录或可预测令牌的安全保证。"""

import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from starlette.requests import Request
from starlette.responses import Response

from errors import LabError

COOKIE_NAME = "__Host-lab_session"
TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+={0,}", re.ASCII)
BEARER = re.compile(r"[Bb][Ee][Aa][Rr][Ee][Rr] +(.+)", re.ASCII)
Clock = Callable[[], float]


@dataclass(frozen=True)
class Principal:
    user_id: str
    can_write: bool
    csrf_token: str


@dataclass(frozen=True)
class Authentication:
    principal: Principal
    token: str
    cookie: bool


class SessionStore(Protocol):
    def resolve(self, token: str) -> Principal | None: ...

    def revoke(self, token: str) -> None: ...


class MemorySessionStore:
    def __init__(self, sessions: list[dict], clock: Clock = time.monotonic):
        self.clock = clock
        created_at = clock()
        self.sessions = {
            session["token"]: {
                **session,
                "expires_at": created_at + session["expires_after_seconds"],
            }
            for session in sessions
        }
        self.lock = RLock()

    def resolve(self, token: str) -> Principal | None:
        with self.lock:
            session = self.sessions.get(token)
            if session is None or session["revoked"] or self.clock() >= session["expires_at"]:
                return None
            return Principal(session["user_id"], session["can_write"], session["csrf_token"])

    def revoke(self, token: str) -> None:
        with self.lock:
            if token in self.sessions:
                self.sessions[token]["revoked"] = True


def ascii_lower(value: str) -> str:
    return value.translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    )


def authenticate(request: Request, store: SessionStore, allow_cookie: bool) -> Authentication:
    authorizations = request.headers.getlist("authorization")
    cookies = []
    for header in request.headers.getlist("cookie"):
        for pair in header.split(";"):
            key, _, value = pair.partition("=")
            if key.strip(" \t") == COOKIE_NAME:
                cookies.append(value.strip(" \t"))
    if (
        len(authorizations) > 1
        or any("," in value for value in authorizations)
        or len(cookies) > 1
        or (authorizations and cookies)
    ):
        raise LabError(400, "ambiguous_credentials")
    cookie = bool(cookies)
    if cookie:
        token = cookies[0] if allow_cookie else ""
    else:
        match = BEARER.fullmatch(authorizations[0]) if authorizations else None
        token = match[1] if match else ""
    if not 1 <= len(token) <= 128 or TOKEN.fullmatch(token) is None:
        raise LabError(401, "authentication_required")
    return Authentication(resolve_principal(store, token), token, cookie)


def resolve_principal(store: SessionStore, token: str) -> Principal:
    try:
        principal = store.resolve(token)
    except Exception:
        raise LabError(503, "session_store_unavailable") from None
    if principal is None:
        raise LabError(401, "authentication_required")
    return principal


def refresh_authentication(auth: Authentication, store: SessionStore) -> Authentication:
    # 上传过程中不持有锁；合法正文完成后，以同一凭据重新获得可信 Principal。
    return Authentication(resolve_principal(store, auth.token), auth.token, auth.cookie)


def csrf_guard(request: Request, auth: Authentication, allowed_origin: str) -> None:
    if not auth.cookie:
        return
    origins = request.headers.getlist("origin")
    csrf = request.headers.getlist("x-csrf-token")
    if (
        len(origins) != 1
        or origins[0] != allowed_origin
        or len(csrf) != 1
        or not hmac.compare_digest(
            csrf[0].encode("utf-8"), auth.principal.csrf_token.encode("utf-8")
        )
    ):
        raise LabError(403, "csrf_failed")


def revoke(store: SessionStore, token: str) -> None:
    try:
        store.revoke(token)
    except Exception:
        raise LabError(503, "session_store_unavailable") from None


def session_cookie(response: Response, token: str, *, clear: bool = False) -> None:
    """仅生成浏览器安全属性；本地 HTTP 请求不能验证 HTTPS Cookie 策略。"""
    response.set_cookie(
        COOKIE_NAME,
        "" if clear else token,
        max_age=0 if clear else None,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
