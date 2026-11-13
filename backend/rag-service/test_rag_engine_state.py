"""RAG session state regression tests without loading the embedding model."""
import pytest

from rag_engine import RAGEngine


@pytest.mark.asyncio
async def test_successful_index_replaces_stale_empty_restore_status(monkeypatch):
    engine = RAGEngine.__new__(RAGEngine)
    engine._restore_states = {}

    async def fake_restore(session_id):
        engine._restore_states[session_id] = "empty_or_unavailable"
        return "empty_or_unavailable"

    async def fake_index(session_id, documents, *, persist):
        assert persist is True
        return {"chunks_added": 147, "total_chunks": 147}

    monkeypatch.setattr(engine, "ensure_session_loaded", fake_restore)
    monkeypatch.setattr(engine, "_index_documents", fake_index)

    result = await engine.index("new-session", [{"name": "report.xlsx"}])

    assert result["chunks_added"] == 147
    assert engine._restore_states["new-session"] == "indexed_in_process"


@pytest.mark.asyncio
async def test_empty_index_keeps_empty_restore_status(monkeypatch):
    engine = RAGEngine.__new__(RAGEngine)
    engine._restore_states = {}

    async def fake_restore(session_id):
        engine._restore_states[session_id] = "empty_or_unavailable"
        return "empty_or_unavailable"

    async def fake_index(session_id, documents, *, persist):
        return {"chunks_added": 0, "total_chunks": 0}

    monkeypatch.setattr(engine, "ensure_session_loaded", fake_restore)
    monkeypatch.setattr(engine, "_index_documents", fake_index)

    await engine.index("empty-session", [])

    assert engine._restore_states["empty-session"] == "empty_or_unavailable"
