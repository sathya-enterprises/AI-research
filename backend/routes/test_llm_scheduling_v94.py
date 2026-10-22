"""v94: LLM call scheduling, source ranking and PDF extraction speed.

These tests mock the Gemini HTTP attempt so they run offline and fast.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from routes import report as R


def _fake_attempts(plan: dict[str, tuple[float, str]], calls: list[str]):
    """plan: key -> (latency_seconds, status). status 'success' returns text."""
    async def fake(user_prompt, depth, key, model):
        calls.append(key)
        latency, status = plan[key]
        await asyncio.sleep(latency)
        if status == "success":
            return f"# Report from {key}", model, "success"
        return "", model, status
    return fake


def _run_wave(monkeypatch, plan, hedge="0.2", budget=5.0):
    calls: list[str] = []
    launched: list[str] = []
    monkeypatch.setattr(R, "_gemini_one_attempt", _fake_attempts(plan, calls))
    monkeypatch.setenv("GEMINI_REPORT_HEDGE_DELAY_SECONDS", hedge)
    started = time.perf_counter()
    result = asyncio.run(R._run_gemini_wave("p", "brief", "m", list(plan), started, started + budget, launched))
    return result, calls, launched, time.perf_counter() - started


def test_fast_success_sends_only_one_request(monkeypatch):
    (text, _m, stats, n), calls, launched, _ = _run_wave(
        monkeypatch, {"k1": (0.05, "success"), "k2": (0.05, "success"), "k3": (0.05, "success")})
    assert text.startswith("# Report from k1")
    assert calls == ["k1"] and launched == ["k1"] and n == 1


def test_503_stops_the_wave_so_scheduler_moves_to_next_model(monkeypatch):
    (text, _m, stats, n), calls, _launched, elapsed = _run_wave(
        monkeypatch, {"k1": (0.02, "503"), "k2": (0.02, "success"), "k3": (0.02, "success")})
    assert text == "" and calls == ["k1"] and stats == {"503": 1}
    assert elapsed < 0.5


def test_fast_key_failure_launches_next_key_immediately(monkeypatch):
    (text, *_rest), calls, _launched, elapsed = _run_wave(
        monkeypatch, {"k1": (0.01, "429"), "k2": (0.05, "success")}, hedge="5")
    assert text.startswith("# Report from k2") and calls == ["k1", "k2"]
    assert elapsed < 1.0  # did not wait for the 5 s hedge delay


def test_slow_request_gets_a_hedge_and_first_answer_wins(monkeypatch):
    (text, *_rest), calls, _launched, elapsed = _run_wave(
        monkeypatch, {"k1": (1.0, "success"), "k2": (0.1, "success"), "k3": (0.1, "success")}, hedge="0.2")
    assert text.startswith("# Report from k2")
    assert calls == ["k1", "k2"]  # k3 never needed
    assert elapsed < 0.8


def test_zero_hedge_delay_keeps_legacy_all_at_once(monkeypatch):
    (_text, *_rest), calls, _launched, _ = _run_wave(
        monkeypatch, {"k1": (0.2, "success"), "k2": (0.05, "success"), "k3": (0.3, "success")}, hedge="0")
    assert sorted(calls) == ["k1", "k2", "k3"]


def test_index_quotes_not_triggered_by_market_share_or_named_index():
    pattern = R._INDEX_QUESTION_RE
    assert not pattern.search("EV adoption in India: sales trends and market share by segment")
    assert not pattern.search("India Electric Mobility Index 2024 state ranking")
    assert pattern.search("How did Nifty 50 perform against Sensex?")
    assert pattern.search("Compare BSE sectoral indices")


def test_topic_ranking_prefers_data_dense_relevant_pages_and_caps_domains():
    q = "Analyze EV adoption in India: electric two-wheelers, three-wheelers, sales and market share"
    noise = [{"title": f"PIB press release {i}", "url": f"https://pib.gov.in/p{i}", "snippet": "Government of India press release",
              "fullContent": "Ministry note on various schemes."} for i in range(6)]
    dash = {"title": "EV sales dashboard", "url": "https://smev.in/statistics",
            "snippet": "Electric two-wheelers three-wheelers sales",
            "fullContent": "| E-2 Wheelers | 252642 | 728054 | 944126 | 1149334 |\n| E-3 Wheelers | 172543 | 401841 | 582793 | 691011 |"}
    internal = {"title": "VERIFIED LIVE INDEX DATA", "url": "internal://verified-index-quotes", "snippet": "", "fullContent": ""}
    other = [{"title": "EV two-wheelers three-wheelers sales report", "url": f"https://site{i}.org/r.pdf",
              "snippet": "electric two-wheelers sales market share", "fullContent": "Sales rose 21% in FY25."} for i in range(2)]
    ranked = R._rank_sources_by_topic([internal] + noise + other + [dash], q, limit=4)
    assert ranked[0] is internal  # internal rows stay first and are not counted
    top_web = ranked[1:5]
    assert dash in top_web  # data-dense dashboard gets a fetch slot ahead of generic PIB pages
    assert sum(1 for s in top_web if "pib.gov.in" in s["url"]) <= 2
    assert len(ranked) == 10  # nothing is dropped


def test_pdf_extraction_respects_time_budget_and_runs_off_loop():
    pypdf = pytest.importorskip("pypdf")
    import glob
    import io
    from utils.source_extractor import _extract_pdf
    fixtures = glob.glob("routes/fixtures/*.pdf")
    if not fixtures:
        pytest.skip("fixture PDF absent")
    reader = pypdf.PdfReader(fixtures[0])
    writer = pypdf.PdfWriter()
    while len(writer.pages) < 60:
        for page in reader.pages:
            writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    t0 = time.perf_counter()
    text = _extract_pdf(buf.getvalue(), 6000, 0.3)
    assert text and time.perf_counter() - t0 < 2.0

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1
        task = asyncio.create_task(ticker())
        await asyncio.to_thread(_extract_pdf, buf.getvalue(), 6000, 0.5)
        task.cancel()
        return ticks
    assert asyncio.run(main()) >= 5  # the event loop kept running during the parse


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self._payload = payload or {}

    def json(self):
        return self._payload


def _audit_payload(status: str) -> dict:
    import json as _json
    body = {"status": status, "summary": "s", "findings": [], "checks": {}, "critical_issues": [], "blocking": status == "FAIL"}
    return {"candidates": [{"content": {"parts": [{"text": _json.dumps(body)}]}}]}


def _patch_audit_http(monkeypatch, behaviour):
    """behaviour(call_index, model) -> (delay_seconds, status_code, verdict|None)."""
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            model = url.split("/models/")[1].split(":")[0]
            idx = len(calls)
            calls.append(model)
            delay, code, verdict = behaviour(idx, model)
            await asyncio.sleep(delay)
            return _FakeResponse(code, _audit_payload(verdict) if verdict else {})

    monkeypatch.setattr(R.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(R, "get_gemini_keys", lambda: ["AIzaKEY1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1", "AIzaKEY2xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx2", "AIzaKEY3xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx3"])
    monkeypatch.setattr(R, "is_rate_limited", lambda *_a, **_k: False)
    return calls


def _audit():
    return asyncio.run(R._gemini_independent_report_audit(
        title="t", question="q", report_text="## A\nText [S1].", report_tier="brief",
        charts=[], images=[], sources=[{"id": "S1", "title": "x", "url": "https://e.org"}],
    ))


def test_audit_sends_one_reviewer_when_it_answers(monkeypatch):
    monkeypatch.setenv("GG_AUDIT_STRATEGY", "hedged")
    calls = _patch_audit_http(monkeypatch, lambda i, m: (0.01, 200, "WARNING"))
    result = _audit()
    assert result["status"] == "WARNING" and len(calls) == 1


def test_audit_hedges_a_slow_reviewer_and_uses_first_valid_verdict(monkeypatch):
    monkeypatch.setenv("GG_AUDIT_STRATEGY", "hedged")
    monkeypatch.setenv("GG_AUDIT_HEDGE_SECONDS", "1")
    calls = _patch_audit_http(monkeypatch, lambda i, m: (3.0, 200, "FAIL") if i == 0 else (0.05, 200, "PASS"))
    t0 = time.perf_counter()
    result = _audit()
    assert result["status"] == "PASS" and len(calls) == 2
    assert calls[0] != calls[1]  # hedge goes to a different model
    assert time.perf_counter() - t0 < 2.5


def test_audit_503_moves_to_next_model_immediately(monkeypatch):
    monkeypatch.setenv("GG_AUDIT_STRATEGY", "hedged")
    monkeypatch.setenv("GG_AUDIT_HEDGE_SECONDS", "20")
    calls = _patch_audit_http(monkeypatch, lambda i, m: (0.01, 503, None) if i == 0 else (0.01, 200, "PASS"))
    t0 = time.perf_counter()
    result = _audit()
    assert result["status"] == "PASS" and len(calls) == 2 and time.perf_counter() - t0 < 1.0


def test_stored_retrieval_can_filter_to_full_text_news_articles(monkeypatch):
    import sys
    import psycopg2
    monkeypatch.setenv("RESEARCH_DATABASE_URL", "postgresql://test-only.invalid/db")

    class _ImportCur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def fetchone(self): return None
        def fetchall(self): return []

    class _ImportConn:
        autocommit = False
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self, *a, **k): return _ImportCur()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    # research_store runs initialise() at import time; stub the DB for the import only.
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: _ImportConn())
    if "utils.research_store" not in sys.modules:
        try:
            import utils.research_store  # noqa: F401
        except Exception as exc:  # schema bootstrap needs a real database
            pytest.skip(f"research_store import needs a database: {type(exc).__name__}")
    from utils import research_store as RS
    captured = {}

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params):
            captured["sql"], captured["params"] = sql, list(params)
        def fetchall(self): return []

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return Cur()

    monkeypatch.setattr(RS, "initialise", lambda: None)
    monkeypatch.setattr(RS, "_connect", lambda: Conn())
    assert RS.retrieve_web_research("India infrastructure output September", 5, 45,
                                    source_type="market_news_article", require_page_text=True) == []
    sql, params = captured["sql"], captured["params"]
    assert "wsl.source_type = %s" in sql and "pageFetchStatus' = 'ok'" in sql
    assert sql.count("%s") == len(params)
    assert params[2:] == [45, "market_news_article", 5]
    # Default call is unchanged
    RS.retrieve_web_research("India infrastructure output", 5)
    assert "source_type = %s" not in captured["sql"] and captured["sql"].count("%s") == len(captured["params"])
