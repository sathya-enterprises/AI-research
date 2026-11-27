"""Who is asking for a RAG session, and whether they may keep asking.

* The principal of a request is decided ONLY from a verified Supabase access token (utils.auth.get_user_id):
  "user:<supabase user id>" when the token verifies, otherwise "anon". Nothing the client puts in a body or a
  custom header can set it. main.py's middleware resolves it for /api/rag and /api/chat requests; report and
  action jobs run inside a copy of the request's context, so the principal follows them onto their threads.
* Session ids must be UUIDs (the browser creates them with crypto.randomUUID: 122 random bits). For an
  anonymous session the id itself is the credential.
* Failed lookups (unknown, unauthorised or malformed session ids) are rate-limited per client: the signed-in
  user, or the forwarded client address for anonymous callers.
* Session ids, tokens and user ids are never logged in clear: ``session_tag`` gives a short one-way hash.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections import deque
from contextvars import ContextVar

ANON = "anon"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_PRINCIPAL: ContextVar[str] = ContextVar("gg_rag_principal", default=ANON)
_CLIENT_KEY: ContextVar[str] = ContextVar("gg_rag_client_key", default="")


def valid_session_id(session_id: object) -> bool:
    return bool(_UUID_RE.match(str(session_id or "").strip()))


def session_tag(session_id: object) -> str:
    """A non-reversible tag for logs (never the id or a prefix of it)."""
    return "s:" + hashlib.sha256(f"gg-rag-session:{session_id}".encode("utf-8")).hexdigest()[:10]


def principal_for(user_id: str | None) -> str:
    uid = str(user_id or "").strip()
    return f"user:{uid}" if valid_session_id(uid) else ANON


def current_principal() -> str:
    return _PRINCIPAL.get()


def set_request_identity(user_id: str | None, client_key: str) -> None:
    _PRINCIPAL.set(principal_for(user_id))
    _CLIENT_KEY.set(str(client_key or ""))


def client_key_from(headers, client_host: str | None) -> str:
    """The rate-limit key for anonymous callers: the first forwarded address, else the socket peer."""
    forwarded = str(headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or str(client_host or "unknown")


def _limit_key() -> str:
    principal = current_principal()
    if principal != ANON:
        return principal
    return "ip:" + hashlib.sha256(_CLIENT_KEY.get().encode("utf-8")).hexdigest()[:16]


class LookupLimiter:
    """At most ``limit`` failed session lookups per ``window`` seconds per caller (GG_RAG_FAILED_LOOKUPS_PER_10MIN,
    default 60). Legitimate use rarely fails a lookup; guessing ids fails every time."""

    def __init__(self, window: float = 600.0) -> None:
        self.window = window
        self._failures: dict[str, deque] = {}
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        try:
            return max(1, int(os.getenv("GG_RAG_FAILED_LOOKUPS_PER_10MIN", "60")))
        except ValueError:
            return 60

    def _prune(self, key: str, now: float) -> deque:
        q = self._failures.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str | None = None, now: float | None = None) -> bool:
        key = key or _limit_key()
        now = time.time() if now is None else now
        with self._lock:
            return len(self._prune(key, now)) >= self.limit

    def failure(self, key: str | None = None, now: float | None = None) -> None:
        key = key or _limit_key()
        now = time.time() if now is None else now
        with self._lock:
            self._prune(key, now).append(now)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


LOOKUPS = LookupLimiter()
