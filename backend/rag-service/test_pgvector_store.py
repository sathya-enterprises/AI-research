"""Offline regression tests for the pgvector storage contract (no live Supabase required)."""
import pytest

import pgvector_store


def test_missing_server_key_fails_clearly(monkeypatch):
    monkeypatch.setattr(pgvector_store, "_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(pgvector_store, "_SERVER_KEY", "")
    with pytest.raises(pgvector_store.PgVectorStoreError, match="server-only"):
        pgvector_store._credentials()


def test_upsert_rejects_wrong_embedding_dimension(monkeypatch):
    monkeypatch.setattr(pgvector_store, "_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(pgvector_store, "_SERVER_KEY", "sb_secret_not_real")
    with pytest.raises(pgvector_store.PgVectorStoreError, match="384 values"):
        pgvector_store.upsert_chunks([{
            "session_id": "s",
            "chunk_id": "c",
            "doc_id": "d",
            "embedding": [0.1, 0.2],
        }])


def test_search_calls_rpc_and_normalizes_scores(monkeypatch):
    captured = {}

    class FakeResponse:
        def json(self):
            return [{
                "session_id": "s",
                "chunk_id": "c",
                "doc_id": "d",
                "text": "evidence text",
                "source_name": "source.pdf",
                "source_type": "file",
                "file_type": "pdf",
                "chunk_index": 0,
                "total_chunks": 1,
                "source_sha256": "abc",
                "original_location": None,
                "source_metadata": {},
                "similarity": 0.82,
            }]

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return FakeResponse()

    monkeypatch.setattr(pgvector_store, "_request", fake_request)
    result = pgvector_store.search_chunks("s", [0.0] * 384, 5)
    assert captured["method"] == "POST"
    assert captured["path"] == "rpc/rag_match_chunks"
    assert captured["json"]["match_session_id"] == "s"
    assert len(captured["json"]["query_embedding"]) == 384
    assert result[0]["score"] == pytest.approx(0.82)
    assert "embedding" not in result[0]


def test_index_engine_has_no_faiss_dependency():
    from pathlib import Path
    source = (Path(__file__).parent / "rag_engine.py").read_text(encoding="utf-8")
    assert "import faiss" not in source
    assert "faiss.IndexFlatIP" not in source
    assert "pgvector_store.search_chunks" in source
    assert "pgvector_store.upsert_chunks" in source


def test_modern_supabase_secret_is_not_treated_as_bearer_token():
    secret = "sb_secret_test_only_not_real"
    headers = pgvector_store._headers(secret)
    assert headers["apikey"] == secret
    assert "Authorization" not in headers


def test_legacy_service_role_jwt_is_sent_as_bearer():
    key = "eyJhbGci.eyJzdXBhYmFzZS10ZXN0.signature"
    headers = pgvector_store._headers(key)
    assert headers["apikey"] == key
    assert headers["Authorization"] == f"Bearer {key}"


def test_rag_service_imports_asyncio_for_query_and_report_handlers():
    """Regression: /query and /report use asyncio.to_thread in main.py."""
    import ast
    from pathlib import Path

    source_path = Path(__file__).with_name("main.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "asyncio" in imported_modules
    source = source_path.read_text(encoding="utf-8")
    assert "asyncio.to_thread(" in source
