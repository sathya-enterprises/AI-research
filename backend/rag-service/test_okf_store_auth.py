import asyncio
import logging
from unittest.mock import patch

import okf_store


class _Response:
    def __init__(self, status_code=200, content=b"ok", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text
        self.is_success = 200 <= status_code < 300

    def json(self):
        return []


class _Client:
    def __init__(self, response, captured):
        self.response = response
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self.captured.update(url=url, **kwargs)
        return self.response


def test_modern_secret_is_apikey_only():
    secret = "sb_secret_test_only_never_a_real_credential"
    headers = okf_store._headers(secret, "text/markdown; charset=utf-8")

    assert headers["apikey"] == secret
    assert "Authorization" not in headers


def test_legacy_jwt_keeps_bearer_authorization():
    legacy_key = "eyJhbGci.eyJzdXBhYmFzZS10ZXN0.signature"

    headers = okf_store._headers(legacy_key)

    assert headers["apikey"] == legacy_key
    assert headers["Authorization"] == f"Bearer {legacy_key}"


def test_modern_publishable_key_is_not_used_as_bearer():
    headers = okf_store._headers("sb_publishable_test_only")

    assert "Authorization" not in headers


def test_missing_storage_credentials_do_not_issue_request(monkeypatch):
    monkeypatch.setattr(okf_store, "_SUPABASE_URL", "")
    captured = {}

    async def run():
        with patch.object(okf_store.httpx, "AsyncClient", side_effect=AssertionError("unexpected request")):
            return await okf_store._put_object("paperly-okf/test/doc.md", "content")

    assert asyncio.run(run()) is False
    assert captured == {}


def test_private_upload_uses_modern_secret_without_bearer(monkeypatch):
    monkeypatch.setattr(okf_store, "_SUPABASE_URL", "https://example.invalid")
    captured = {}
    secret = "sb_secret_test_only_never_a_real_credential"

    async def run():
        with patch.object(okf_store.httpx, "AsyncClient", return_value=_Client(_Response(), captured)):
            return await okf_store._put_object(
                "paperly-okf/session/doc.md", "content", bucket="rag-knowledge-private", key=secret
            )

    assert asyncio.run(run()) is True
    assert captured["headers"]["apikey"] == secret
    assert "Authorization" not in captured["headers"]


def test_legacy_upload_uses_jwt_bearer(monkeypatch):
    monkeypatch.setattr(okf_store, "_SUPABASE_URL", "https://example.invalid")
    captured = {}
    legacy_key = "eyJhbGci.eyJzdXBhYmFzZS10ZXN0.signature"

    async def run():
        with patch.object(okf_store.httpx, "AsyncClient", return_value=_Client(_Response(), captured)):
            return await okf_store._put_object("paperly-okf/session/doc.md", "content", key=legacy_key)

    assert asyncio.run(run()) is True
    assert captured["headers"]["apikey"] == legacy_key
    assert captured["headers"]["Authorization"] == f"Bearer {legacy_key}"


def test_storage_auth_failure_is_sanitized_and_reported(monkeypatch, caplog):
    monkeypatch.setattr(okf_store, "_SUPABASE_URL", "https://example.invalid")
    captured = {}
    secret = "sb_secret_test_only_never_a_real_credential"
    response = _Response(403, text="permission denied; internal response must not be logged")

    async def run():
        with patch.object(okf_store.httpx, "AsyncClient", return_value=_Client(response, captured)):
            return await okf_store._put_object("paperly-okf/session/doc.md", "content", key=secret)

    with caplog.at_level(logging.WARNING, logger="rag-okf"):
        assert asyncio.run(run()) is False

    assert "authentication_or_permission" in caplog.text
    assert secret not in caplog.text
    assert "internal response must not be logged" not in caplog.text
