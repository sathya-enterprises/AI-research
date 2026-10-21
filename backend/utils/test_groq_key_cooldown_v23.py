"""Regression tests for shared, atomic Groq key rotation cooldown (v23)."""
from __future__ import annotations

from utils import keys


def _reset_key_state(monkeypatch):
    monkeypatch.setattr(keys, "_groq_key_cooldown_until", {})
    monkeypatch.setattr(keys, "_groq_key_last_reserved", {})
    monkeypatch.setattr(keys, "_rate_limited_until", {})
    monkeypatch.setattr(keys, "_usage_count", {})


def test_groq_key_reservation_rotates_and_refuses_reuse_within_60_seconds(monkeypatch):
    _reset_key_state(monkeypatch)
    monkeypatch.setattr(keys, "_GROQ_KEY_COOLDOWN_MS", 60_000)
    now = [1000.0]
    monkeypatch.setattr(keys.time, "time", lambda: now[0])
    pool = ["groq-1", "groq-2", "groq-3"]

    first = keys.reserve_groq_key(pool)
    second = keys.reserve_groq_key(pool)
    third = keys.reserve_groq_key(pool)

    assert [first, second, third] == pool
    assert keys.reserve_groq_key(pool) is None
    assert keys.available_groq_key_count(pool) == 0

    now[0] += 60.01
    assert keys.reserve_groq_key(pool) == "groq-1"


def test_groq_key_reservation_respects_existing_rate_limit(monkeypatch):
    _reset_key_state(monkeypatch)
    monkeypatch.setattr(keys, "_GROQ_KEY_COOLDOWN_MS", 60_000)
    now = [1000.0]
    monkeypatch.setattr(keys.time, "time", lambda: now[0])
    keys._rate_limited_until["groq-bad"] = now[0] + 86_400

    assert keys.reserve_groq_key(["groq-bad", "groq-good"]) == "groq-good"


def test_groq_reservations_are_shared_with_environment_pool(monkeypatch):
    _reset_key_state(monkeypatch)
    monkeypatch.setattr(keys, "_GROQ_KEY_COOLDOWN_MS", 60_000)
    now = [1000.0]
    monkeypatch.setattr(keys.time, "time", lambda: now[0])
    monkeypatch.setenv("GROQ_API_KEYS", "one,two")

    assert keys.reserve_groq_key() == "one"
    assert keys.reserve_groq_key() == "two"
    assert list(keys.iter_reserved_groq_keys()) == []
