import json
from pathlib import Path

from utils import research_capture as rc


def test_capture_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("GG_RESEARCH_CAPTURE", raising=False)
    monkeypatch.setenv("GG_RESEARCH_CAPTURE_DIR", str(tmp_path))
    token = rc.begin_research_capture("public test question", "brief")
    assert token is None
    rc.record_research_stage("should_not_write", {"x": 1})
    assert list(tmp_path.glob("*.json")) == []
    assert rc.finish_research_capture({"status": "no-op"}) is None


def test_capture_records_raw_exa_and_normalized_results_without_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("GG_RESEARCH_CAPTURE", "1")
    monkeypatch.setenv("GG_RESEARCH_CAPTURE_DIR", str(tmp_path))
    token = rc.begin_research_capture("renewable energy data", "brief")
    rc.record_exa_raw_response(
        "renewable energy data",
        {"query": "renewable energy data", "type": "auto", "api_key": "never-save-this"},
        [{
            "title": "Official capacity report",
            "url": "https://example.org/report?utm_source=test&api_key=SECRET",
            "snippet": "Search snippet with 100 GW",
            "fullContent": "Official report says installed capacity reached 100 GW.",
            "score": 0.93,
            "highlights": ["Installed capacity reached 100 GW."],
        }],
        transport="exa-rest",
        key_suffix="1234",
    )
    rc.record_search_call(
        ["renewable energy data"],
        [[{"title": "Official capacity report", "url": "https://example.org/report", "snippet": "Search snippet", "fullContent": "Installed capacity reached 100 GW."}]],
        max_results=10,
    )
    rc.record_research_stage("sources_after_article_fetch", {
        "sources": [{"title": "Official capacity report", "url": "https://example.org/report", "pageFetchStatus": "ok", "pageContent": "Installed capacity reached 100 GW."}]
    })
    path = rc.finish_research_capture({"report": "Capacity reached 100 GW [S1]."}, token=token)
    assert path is not None
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["status"] == "completed"
    assert data["rawExaResponses"][0]["results"][0]["fullContent"].startswith("Official report")
    assert data["searchCalls"][0]["perQuery"][0]["resultCount"] == 1
    assert "never-save-this" not in open(path, encoding="utf-8").read()
    assert "SECRET" not in open(path, encoding="utf-8").read()
    assert "REDACTED" in data["rawExaResponses"][0]["results"][0]["url"]


def test_capture_omits_uploaded_document_text(monkeypatch, tmp_path):
    monkeypatch.setenv("GG_RESEARCH_CAPTURE", "1")
    monkeypatch.setenv("GG_RESEARCH_CAPTURE_DIR", str(tmp_path))
    token = rc.begin_research_capture("summarize attached doc", "brief")
    rc.record_research_stage("sources", {"sources": [{
        "title": "Private uploaded report", "url": "internal://uploaded/file1",
        "source_type": "Uploaded document", "pageContent": "This private content must not be captured.",
    }]})
    path = rc.finish_research_capture(status="completed", token=token)
    data = json.loads(open(path, encoding="utf-8").read())
    serialized = json.dumps(data)
    assert "This private content must not be captured" not in serialized
    assert "omitted from diagnostic capture" in serialized


def test_exa_multi_search_records_each_query_separately(monkeypatch, tmp_path):
    import asyncio
    from routes import chat

    monkeypatch.setenv("GG_RESEARCH_CAPTURE", "1")
    monkeypatch.setenv("GG_RESEARCH_CAPTURE_DIR", str(tmp_path))
    token = rc.begin_research_capture("two-angle research", "brief")

    async def fake_search(query, max_results=20, min_results=10, images_out=None,
                          historical_intent=False, max_keys_per_query=None, topic=None, region=None,
                          time_range=None, page_enrich_limit=2, fast_chat=False, **kwargs):
        return [{
            "title": f"result for {query}", "url": f"https://example.org/{query.replace(' ', '-')}",
            "snippet": f"snippet {query}", "fullContent": f"source text for {query}", "score": 0.8,
        }]

    monkeypatch.setattr(chat, "exa_search", fake_search)
    results = asyncio.run(chat.exa_search_multi(["capacity official data", "investment trends data"], max_results=5))
    path = rc.finish_research_capture({"resultCount": len(results)}, token=token)
    data = json.loads(open(path, encoding="utf-8").read())
    assert len(results) == 2
    assert len(data["searchCalls"]) == 1
    assert [x["query"] for x in data["searchCalls"][0]["perQuery"]] == [
        "capacity official data", "investment trends data"
    ]
    assert data["searchCalls"][0]["perQuery"][1]["results"][0]["fullContent"] == "source text for investment trends data"


def test_capture_path_is_absolute_and_finish_without_token_resets_context(monkeypatch, tmp_path):
    monkeypatch.setenv("GG_RESEARCH_CAPTURE", "1")
    monkeypatch.setenv("GG_RESEARCH_CAPTURE_DIR", str(tmp_path))
    rc.begin_research_capture("capture path regression", "detailed")
    path = rc.current_research_capture_path()
    assert path is not None
    assert Path(path).is_absolute()
    assert Path(path).exists()
    saved = rc.finish_research_capture({"reason": "test early-return finalizer"}, status="incomplete")
    assert saved == path
    assert not rc.current_capture_active()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["status"] == "incomplete"
    assert data["finishedAt"]


def test_capture_auto_finalizes_if_async_request_exits_early(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setenv("GG_RESEARCH_CAPTURE", "1")
    monkeypatch.setenv("GG_RESEARCH_CAPTURE_DIR", str(tmp_path))

    async def run_early_request():
        rc.begin_research_capture("early return test", "detailed")
        path = rc.current_research_capture_path()
        assert path is not None
        return path

    path = asyncio.run(run_early_request())
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["status"] == "incomplete"
    assert data["finishedAt"]
    assert data["stages"]["request_exit"]["data"]["reason"] == "request task exited before normal capture finalization"


def test_default_capture_path_is_directly_under_backend_diagnostics(monkeypatch):
    monkeypatch.delenv("GG_RESEARCH_CAPTURE_DIR", raising=False)
    ctx = {"captureId": "regression-test-capture"}
    path = rc._path_for(ctx)
    expected_root = Path(rc.__file__).resolve().parents[1] / "diagnostics"
    assert path.parent == expected_root.resolve()
    assert path.name == "research_capture_regression-test-capture.json"
