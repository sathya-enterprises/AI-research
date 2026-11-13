"""Offline regression tests for the pgvector storage contract (no live Supabase required)."""
import pytest

import pgvector_store


class _FakeCursor:
    def __init__(self, *, names=(), rows=(), batches=(), one=None):
        self.description = [type("Column", (), {"name": name}) for name in names]
        self.rows = list(rows)
        self.batches = list(batches)
        self.one = one
        self.executed = []
        self.executemany_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def executemany(self, query, params):
        self.executemany_calls.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchmany(self, _size):
        return self.batches.pop(0) if self.batches else []

    def fetchone(self):
        return self.one


class _FakeConnection:
    closed = 0

    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor


class _FakePool:
    def __init__(self, cursor):
        self.connection = _FakeConnection(cursor)
        self.returned = []

    def getconn(self):
        return self.connection

    def putconn(self, connection, close=False):
        self.returned.append((connection, close))


def _use_direct_postgres(monkeypatch, cursor):
    pool = _FakePool(cursor)
    monkeypatch.setattr(pgvector_store, "_SUPABASE_URL", "")
    monkeypatch.setattr(pgvector_store, "_SERVER_KEY", "")
    monkeypatch.setattr(pgvector_store, "_ANON_KEY", "public-key-must-not-be-used")
    monkeypatch.setattr(pgvector_store, "_DATABASE_URL", "postgresql://private-test-dsn")
    monkeypatch.setattr(pgvector_store, "_database_pool", lambda: pool)
    return pool


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


def test_storage_mode_uses_private_database_url_without_public_key(monkeypatch):
    monkeypatch.setattr(pgvector_store, "_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(pgvector_store, "_SERVER_KEY", "")
    monkeypatch.setattr(pgvector_store, "_ANON_KEY", "public-key-must-not-be-used")
    monkeypatch.setattr(pgvector_store, "_DATABASE_URL", "postgresql://private-test-dsn")
    assert pgvector_store._storage_mode() == "postgres"
    assert pgvector_store.storage_backend() == "postgresql_pgvector"


def test_postgres_upsert_uses_parameterized_values_and_validates_vector(monkeypatch):
    cursor = _FakeCursor()
    pool = _use_direct_postgres(monkeypatch, cursor)
    monkeypatch.setattr(pgvector_store, "_request", lambda *_args, **_kwargs: pytest.fail("REST path used"))
    row = {
        "session_id": "test-session",
        "chunk_id": "chunk-1",
        "doc_id": "workbook-1",
        "text": "Revenue grew from 10 to 12.",
        "source_name": "metrics.xlsx",
        "source_type": "file",
        "file_type": "xlsx",
        "chunk_index": 0,
        "total_chunks": 1,
        "source_sha256": "abc123",
        "original_location": "Sheet1!A1:B2",
        "source_metadata": {"sheet_name": "Sheet1"},
        "embedding": [0.1] * 384,
    }

    pgvector_store.upsert_chunks([row])

    assert len(cursor.executemany_calls) == 1
    query, values = cursor.executemany_calls[0]
    assert "INSERT INTO public.rag_chunks" in query
    assert "ON CONFLICT (session_id, chunk_id)" in query
    assert values[0][0:3] == ("test-session", "chunk-1", "workbook-1")
    assert values[0][11] == '{"sheet_name": "Sheet1"}'
    assert values[0][12].startswith("[0.1,0.1,")
    assert pool.returned == [(pool.connection, False)]


def test_postgres_search_calls_match_rpc_and_normalizes_results(monkeypatch):
    names = (
        "session_id", "chunk_id", "doc_id", "text", "source_name", "source_type",
        "file_type", "chunk_index", "total_chunks", "source_sha256",
        "original_location", "source_metadata", "similarity",
    )
    row = (
        "test-session", "chunk-1", "workbook-1", "year 2024 revenue 12", "metrics.xlsx",
        "file", "xlsx", 0, 1, "abc123", "Sheet1!A1:B2", {"sheet_name": "Sheet1"}, 0.91,
    )
    cursor = _FakeCursor(names=names, rows=[row])
    _use_direct_postgres(monkeypatch, cursor)

    result = pgvector_store.search_chunks("test-session", [0.0] * 384, 5)

    query, params = cursor.executed[0]
    assert "FROM public.rag_match_chunks(%s::vector, %s, %s)" in query
    assert params[1:] == ("test-session", 5)
    assert params[0].startswith("[0,0,")
    assert result[0]["score"] == pytest.approx(0.91)
    assert result[0]["source_metadata"] == {"sheet_name": "Sheet1"}
    assert "embedding" not in result[0]


def test_postgres_session_load_reads_metadata_without_embeddings(monkeypatch):
    names = (
        "session_id", "chunk_id", "doc_id", "text", "source_name", "source_type",
        "file_type", "chunk_index", "total_chunks", "source_sha256",
        "original_location", "source_metadata",
    )
    row = (
        "test-session", "chunk-1", "workbook-1", "table row", "metrics.xlsx", "file",
        "xlsx", 0, 1, "abc123", "Sheet1!A1:B2", {"sheet_name": "Sheet1"},
    )
    cursor = _FakeCursor(names=names, batches=[[row]])
    _use_direct_postgres(monkeypatch, cursor)

    result = pgvector_store.get_session_chunks("test-session")

    assert result[0]["chunk_index"] == 0
    assert result[0]["source_metadata"] == {"sheet_name": "Sheet1"}
    assert "embedding" not in result[0]
    assert "WHERE session_id = %s" in cursor.executed[0][0]
    assert cursor.executed[0][1] == ("test-session",)


def test_postgres_health_checks_extension_table_rpc_and_permissions(monkeypatch):
    cursor = _FakeCursor(one=(True, True, True, True, True, True, True))
    _use_direct_postgres(monkeypatch, cursor)

    health = pgvector_store.health_check()

    assert health == {
        "backend": "postgresql_pgvector", "configured": True,
        "ready": True, "table": "rag_chunks",
    }


def test_postgres_deletes_are_scoped_to_session_and_document(monkeypatch):
    cursor = _FakeCursor()
    _use_direct_postgres(monkeypatch, cursor)

    pgvector_store.delete_document("test-session", "workbook-1")
    pgvector_store.delete_session("test-session")

    assert cursor.executed[0][1] == ("test-session", "workbook-1")
    assert "session_id = %s AND doc_id = %s" in cursor.executed[0][0]
    assert cursor.executed[1][1] == ("test-session",)
    assert "DELETE FROM public.rag_chunks WHERE session_id = %s" in cursor.executed[1][0]


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
