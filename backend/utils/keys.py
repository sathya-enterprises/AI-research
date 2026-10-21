"""
Key-pool management — mirrors the TypeScript helpers in the old Next.js routes.

Long-duration bans (403 → key banned 24h) are additionally persisted to
Supabase. Without this, every redeploy wiped the in-memory
_rate_limited_until dict below, so a key that was 403-banned yesterday looked
completely fresh again on next boot — it got retried, usually 403'd again
immediately, and burned an attempt slot inside call_gemini()'s time-budgeted
loop (see routes/report.py) that a working key could have used instead. On a
setup that redeploys frequently, this meant the "24h ban" rarely lasted more
than a few minutes in practice.

Short transient throttles (429/503 backoffs of 30s/60s/120s) are NOT
persisted — they're cheap to rediscover and don't need to survive a restart;
persisting them would just spam Supabase with writes on every rate limit.

Requires a Supabase table (see supabase_key_bans.sql in this directory).
If SUPABASE_URL/SUPABASE_ANON_KEY aren't set, or the table doesn't exist,
every persistence call is a silent no-op — behavior degrades to the old
in-memory-only behavior, it doesn't break.
"""
import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from threading import Lock

import httpx

log = logging.getLogger("keys")

_lock = Lock()
_rate_limited_until: dict[str, float] = {}
_usage_count: dict[str, int] = {}
# A separate short cooldown for Groq requests: a healthy key is used at most
# once per 60 seconds across chat, planner, charts, and report-generation paths.
# This is process-local coordination; provider 429/401 state remains in
# _rate_limited_until and is longer-lived when appropriate.
_GROQ_KEY_COOLDOWN_MS = max(1_000, int(os.environ.get("GROQ_KEY_COOLDOWN_MS", "60000")))
_groq_key_cooldown_until: dict[str, float] = {}
_groq_key_last_reserved: dict[str, float] = {}
# Parallel report tasks lease keys atomically. This prevents two concurrent report
# jobs from assigning the same API key to independent workers at the same time.
_pool_key_leases: dict[tuple[str, str], float] = {}
_pool_key_last_reserved: dict[tuple[str, str], float] = {}


def reserve_provider_key(
    pool: list[str], namespace: str, *, model: str | None = None,
    lease_ms: int = 90_000, exclude: set[str] | None = None,
) -> str | None:
    """Atomically lease one healthy provider key for a parallel task.

    Cooldowns are scoped to the actual key and provider/model labels. Keys that
    belong to different cloud projects/accounts can therefore be used as separate
    quota buckets, while a key already leased by another in-process report job is
    not assigned again until released or its safety lease expires.
    """
    keys = list(dict.fromkeys(str(key).strip() for key in (pool or []) if str(key).strip()))
    if not keys:
        return None
    now = time.time()
    ns = str(namespace or "provider").strip()
    skipped = set(exclude or ())
    lease_seconds = max(5.0, min(180.0, int(lease_ms) / 1000.0))
    with _lock:
        for lease_id, until in list(_pool_key_leases.items()):
            if until <= now:
                _pool_key_leases.pop(lease_id, None)
        eligible = []
        for key in keys:
            lease_id = (ns, key)
            if key in skipped or lease_id in _pool_key_leases:
                continue
            labels = (key, f"{ns}:{key}")
            if model:
                labels += (f"{ns}:{key}:{model}",)
            if any(_rate_limited_until.get(label, 0.0) > now for label in labels):
                continue
            eligible.append(key)
        if not eligible:
            return None
        key = min(eligible, key=lambda item: (
            _pool_key_last_reserved.get((ns, item), 0.0),
            _usage_count.get(item, 0),
        ))
        _pool_key_leases[(ns, key)] = now + lease_seconds
        _pool_key_last_reserved[(ns, key)] = now
        _usage_count[key] = _usage_count.get(key, 0) + 1
        return key


def release_provider_key(key: str, namespace: str, cooldown_ms: int = 0) -> None:
    """Release a parallel-task lease; optional cooldown does not clear a provider ban."""
    value = str(key or "").strip()
    ns = str(namespace or "provider").strip()
    if not value:
        return
    now = time.time()
    with _lock:
        _pool_key_leases.pop((ns, value), None)
        delay = max(0, int(cooldown_ms)) / 1000.0
        if delay:
            _pool_key_leases[(ns, value)] = now + delay

# ─── Persistence (Supabase) ────────────────────────────────────────────────
# Only bans lasting at least this long get written — see module docstring.
_PERSIST_MIN_MS = 60 * 60_000  # 1 hour — clears the bar for 24h 403 bans,
                                # not for 30s/60s/120s throttle backoffs.

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


def _sb_headers() -> dict:
    return {
        "apikey": _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _key_hash(key: str) -> str:
    """Never store raw API keys — just enough of a hash to re-match against
    this process's own configured pool on the next startup."""
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


async def _persist_ban(key: str, until_epoch: float) -> None:
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                f"{_SUPABASE_URL}/rest/v1/gg_key_bans",
                headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
                json={"key_hash": _key_hash(key), "banned_until": _iso(until_epoch)},
            )
            if r.status_code in (401, 403, 404):
                log.debug(
                    "Supabase gg_key_bans: insert blocked/table missing (%d) — "
                    "run supabase_key_bans.sql, or bans stay in-memory only",
                    r.status_code,
                )
    except Exception as exc:
        log.debug("Supabase persist_ban failed (non-critical): %s", exc)


async def load_persisted_bans(*pools: list[str]) -> None:
    """Call once at startup with every key pool in use (Gemini, Groq, ...).
    Re-applies any still-active persisted bans to whichever of today's
    configured keys they match, so a key 403-banned yesterday stays banned
    across this redeploy instead of starting from zero."""
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return
    all_keys = [k for pool in pools for k in pool]
    if not all_keys:
        return
    hash_to_key = {_key_hash(k): k for k in all_keys}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{_SUPABASE_URL}/rest/v1/gg_key_bans",
                headers=_sb_headers(),
                params={"select": "key_hash,banned_until", "banned_until": f"gt.{_iso(time.time())}"},
            )
            if not r.is_success:
                log.debug("Supabase gg_key_bans: load returned %d — skipping restore", r.status_code)
                return
            rows = r.json()
    except Exception as exc:
        log.debug("Supabase load_persisted_bans failed (non-critical): %s", exc)
        return

    restored = 0
    with _lock:
        for row in rows:
            key = hash_to_key.get(row.get("key_hash"))
            if not key:
                continue  # ban belongs to a key no longer in this deploy's env
            try:
                until_epoch = datetime.fromisoformat(row["banned_until"].replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            _rate_limited_until[key] = until_epoch
            restored += 1
    if restored:
        log.info("Key bans: restored %d still-active ban(s) from Supabase after restart", restored)


def _load_pool(env_var: str) -> list[str]:
    """
    Split a Render env var into individual keys.

    Historically this only split on ",". That silently breaks when someone
    pastes multiple keys into Render's env-var editor separated by newlines
    (a common paste mistake with multi-line text boxes) or semicolons —
    the whole blob is then treated as a single, garbled "key" that will
    reliably 401. Splitting on comma, semicolon, and newline (any mix of
    them) and stripping stray surrounding quotes fixes that without
    affecting a correctly-configured single comma-separated value at all.
    """
    raw = os.environ.get(env_var, "")
    parts = re.split(r"[,;\n]+", raw)
    keys = []
    for k in parts:
        k = k.strip().strip('"').strip("'").strip()
        if k:
            keys.append(k)
    return keys


def describe_pool(pool: list[str]) -> str:
    """
    Masked, non-secret summary of a key pool for startup/debug logging —
    Returns only the key count. No key material or suffix is included.
    """
    if not pool:
        return "0 key(s)"
    return f"{len(pool)} key(s)"


def get_groq_keys()   -> list[str]: return _load_pool("GROQ_API_KEYS")
def get_exa_keys() -> list[str]:
    """Return deduplicated Exa API keys in configured order.

    Supports EXA_API_KEY, EXA_API_KEY_2, EXA_API_KEY_3, plus EXA_API_KEYS for
    any additional comma-, semicolon-, or newline-separated keys. Duplicate
    values are removed while preserving configuration order.
    """
    pool = (
        _load_pool("EXA_API_KEY")
        + _load_pool("EXA_API_KEY_2")
        + _load_pool("EXA_API_KEY_3")
        + _load_pool("EXA_API_KEYS")
    )
    return list(dict.fromkeys(pool))

def get_gemini_keys() -> list[str]: return _load_pool("GEMINI_API_KEY")
def get_serpapi_keys() -> list[str]: return _load_pool("SERPAPI_API_KEY") + _load_pool("SERPAPI_API_KEYS")
def get_serper_keys() -> list[str]:
    """Return deduplicated Serper keys from both supported env spellings."""
    return list(dict.fromkeys(_load_pool("SERPER_API_KEY") + _load_pool("SERPER_API_KEYS")))


def pick_key(pool: list[str], label: str) -> str:
    if not pool:
        raise ValueError(f"No {label} keys configured")
    now = time.time()
    with _lock:
        available = [k for k in pool if _rate_limited_until.get(k, 0) < now]
        candidates = available if available else pool
        candidates.sort(key=lambda k: _usage_count.get(k, 0))
        key = candidates[0]
        _usage_count[key] = _usage_count.get(key, 0) + 1
    return key



def available_groq_key_count(pool: list[str] | None = None) -> int:
    """Count Groq keys eligible now, honoring both 60s use cooldowns and bans."""
    keys = list(dict.fromkeys(pool if pool is not None else get_groq_keys()))
    now = time.time()
    with _lock:
        return sum(
            1 for key in keys
            if _rate_limited_until.get(key, 0) <= now
            and _groq_key_cooldown_until.get(key, 0) <= now
        )


def reserve_groq_key(pool: list[str] | None = None, cooldown_ms: int | None = None) -> str | None:
    """Atomically reserve one eligible Groq key for a logical request.

    A key is immediately placed on a short cooldown, so concurrent requests
    cannot select it again during the next minute. If every key is cooling down
    or provider-rate-limited, return None rather than reusing one.
    """
    keys = list(dict.fromkeys(pool if pool is not None else get_groq_keys()))
    if not keys:
        return None
    now = time.time()
    delay_ms = _GROQ_KEY_COOLDOWN_MS if cooldown_ms is None else max(1_000, int(cooldown_ms))
    with _lock:
        eligible = [
            key for key in keys
            if _rate_limited_until.get(key, 0) <= now
            and _groq_key_cooldown_until.get(key, 0) <= now
        ]
        if not eligible:
            return None
        # Least-recently reserved first; usage count is a deterministic tie-breaker.
        key = min(eligible, key=lambda item: (
            _groq_key_last_reserved.get(item, 0.0),
            _usage_count.get(item, 0),
        ))
        _groq_key_cooldown_until[key] = now + delay_ms / 1000
        _groq_key_last_reserved[key] = now
        _usage_count[key] = _usage_count.get(key, 0) + 1
        return key


def iter_reserved_groq_keys(pool: list[str] | None = None, max_keys: int | None = None):
    """Yield reserved keys one at a time, reserving each only when attempted."""
    keys = list(dict.fromkeys(pool if pool is not None else get_groq_keys()))
    limit = len(keys) if max_keys is None else min(len(keys), max(0, int(max_keys)))
    for _ in range(limit):
        key = reserve_groq_key(keys)
        if key is None:
            return
        yield key


def mark_rate_limited(key: str, ms: int = 60_000) -> None:
    until_epoch = time.time() + ms / 1000
    with _lock:
        _rate_limited_until[key] = until_epoch
    # Persist only long-lived bans (see _PERSIST_MIN_MS) — fire-and-forget,
    # never blocks or raises into the caller.
    if ms >= _PERSIST_MIN_MS:
        try:
            asyncio.create_task(_persist_ban(key, until_epoch))
        except RuntimeError:
            # No running event loop (e.g. sync call site) — the in-memory
            # ban above still applies for the lifetime of this process.
            pass


def is_rate_limited(key: str) -> bool:
    return _rate_limited_until.get(key, 0) > time.time()


def round_robin(pool: list[str]) -> list[str]:
    """Return pool sorted by least-used first (excluding currently RL'd keys)."""
    now = time.time()
    available = [k for k in pool if _rate_limited_until.get(k, 0) < now]
    result = sorted(available, key=lambda k: _usage_count.get(k, 0))
    # append RL'd keys at the end as last resort
    rl = [k for k in pool if k not in result]
    return result + rl
