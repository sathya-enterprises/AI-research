"""POST /rerank: stateless cross-encoder scores for report evidence, behind the optional shared secret.
The heavy modules (models, pgvector, OKF storage) are replaced by stubs, so no model is loaded."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


class _Engine:
    reranker_ready = True
    model_name = "stub"
    _dim = 384
    _reranker = object()

    def rerank_scores(self, query, texts):
        return [float(len(t)) for t in texts]


@pytest.fixture()
def client(monkeypatch):
    stubs = {"rag_engine": types.SimpleNamespace(RAGEngine=_Engine),
             "okf_store": types.SimpleNamespace(), "pgvector_store": types.SimpleNamespace(storage_backend=lambda: "stub")}
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    # load rag-service/main.py by path: in the full suite "main" would be the backend's own main.py
    spec = importlib.util.spec_from_file_location("rag_service_main_under_test", Path(__file__).with_name("main.py"))
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    yield TestClient(main.app), main


BODY = {"query": "electric two-wheeler registrations", "passages": [{"id": "S1.1", "text": "abc"}, {"id": "S2.1", "text": "abcdef"}]}


def test_rerank_returns_a_score_per_passage(client, monkeypatch):
    monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)
    http, _ = client
    res = http.post("/rerank", json=BODY)
    assert res.status_code == 200 and res.json()["scores"] == {"S1.1": 3.0, "S2.1": 6.0}


def test_rerank_requires_the_shared_secret_when_one_is_configured(client, monkeypatch, caplog):
    monkeypatch.setenv("RAG_SERVICE_TOKEN", "test-only-shared-secret")
    http, _ = client
    assert http.post("/rerank", json=BODY).status_code == 401
    assert http.post("/rerank", json=BODY, headers={"X-RAG-Service-Token": "wrong"}).status_code == 401
    assert http.post("/rerank", json=BODY, headers={"X-RAG-Service-Token": "test-only-shared-secret"}).status_code == 200
    assert "test-only-shared-secret" not in caplog.text


def test_rerank_is_503_without_a_reranker_and_rejects_oversized_requests(client, monkeypatch):
    monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)
    http, main = client
    monkeypatch.setattr(main.engine, "reranker_ready", False, raising=False)
    assert http.post("/rerank", json=BODY).status_code == 503
    monkeypatch.setattr(main.engine, "reranker_ready", True, raising=False)
    too_many = {"query": "q", "passages": [{"id": f"S{i}.1", "text": "x"} for i in range(65)]}
    assert http.post("/rerank", json=too_many).status_code == 422
