from __future__ import annotations

import asyncio
from types import SimpleNamespace

from routes import report
from utils import site_crawler


def test_planner_normalizes_common_json_schema_drift():
    raw = {
        "outline": [
            {"title": "Investor options", "description": "Compare evidence-backed targets", "format": "narrative"},
            {"name": "Outreach priorities", "focus": "Rank next actions", "content_format": "list"},
        ],
        "depth": "detailed",
    }
    normalized = report._normalize_report_plan_shape(raw, "detailed")
    assert normalized is not None
    assert report._validate_report_plan(normalized)
    assert [s["heading"] for s in normalized["sections"]] == ["Investor options", "Outreach priorities"]
    assert normalized["sections"][0]["format"] == "prose"
    assert normalized["sections"][1]["format"] == "bullets"


def test_local_investor_fallback_plan_scales_by_selected_tier():
    intent = SimpleNamespace(
        resolved_topic="investors for software projects in Electronic City, Bengaluru",
        evidence_needed=["general_knowledge"],
        intent_label="investor discovery",
    )
    counts = {}
    for tier in ("brief", "detailed", "in_depth"):
        plan = report._fallback_report_plan("give as a report", intent=intent, report_tier=tier)
        counts[tier] = len(plan["sections"])
        assert all(section["heading"] for section in plan["sections"])
        assert plan["tier"] == tier
    assert counts["brief"] == 2
    assert counts["detailed"] == 5
    assert counts["in_depth"] == 7


def test_automatic_tier_completion_fills_missing_planned_sections(monkeypatch):
    base = "## Investor options\n\nThe initial report compares a few potential investors using verified source evidence [S1]."
    plan = {"sections": [
        {"heading": "Investor options", "purpose": "Compare possible targets", "format": "mixed"},
        {"heading": "Funding stage and mandate", "purpose": "Compare published stage and mandate", "format": "table"},
        {"heading": "Outreach priorities", "purpose": "Recommend verification and next steps", "format": "bullets"},
        {"heading": "Risks and evidence gaps", "purpose": "State unknowns", "format": "bullets"},
    ]}
    calls = []

    async def fake_continuation(prompt, tier):
        calls.append((prompt, tier))
        import re
        headings = re.findall(r"\[\[SECTION TO COMPLETE\]\] (.+)", prompt)
        body = ("The available source evidence identifies this as a candidate to verify rather than confirmed fit. "
                "Check the published investment thesis, active fund status, relevant software portfolio, stage, "
                "location and an official contact route before outreach. The source material does not establish "
                "a ticket size unless the investor publishes one. [S1]")
        return "\n\n".join(f"[[SECTION: {h}]]\n{h}: {body} Additional section-specific guidance about {h.lower()} and the source-backed verification steps.\n[[END SECTION]]" for h in headings), "mock-continuation"

    monkeypatch.setattr(report, "_call_gemini_plain_continuation", fake_continuation)
    evidence = "\n\n".join(f"[S1] Source evidence {i}: published investor portfolio and software funding mandate details." for i in range(30))
    out = asyncio.run(report._expand_report_to_tier(base, "investors for software projects in Electronic City, Bengaluru", "detailed", plan, evidence))
    assert calls
    assert "## Funding stage and mandate" in out
    assert "## Outreach priorities" in out
    assert "## Risks and evidence gaps" in out
    assert len(out) > len(base) + 500
    assert "[[SECTION:" not in out


def test_crawler_url_safety_and_same_site_restrictions():
    assert site_crawler._safe_url("https://example.com/portfolio?utm_source=test#top") == "https://example.com/portfolio"
    assert site_crawler._safe_url("http://127.0.0.1/private") is None
    assert site_crawler._safe_url("https://example.com/report.pdf") is None
    assert site_crawler._same_site("https://portfolio.example.com/fund", "https://example.com/companies")
    assert not site_crawler._same_site("https://example.org/portfolio", "https://example.com/companies")


def test_crawler_follows_relevant_internal_pages_and_records_provenance(monkeypatch):
    class FakeResponse:
        def __init__(self, url, html):
            self.url = url
            self.text = html
            self.status_code = 200
            self.is_success = True
            self.headers = {"content-type": "text/html; charset=utf-8"}

    pages = {
        "https://example.com/investors": """<html><head><title>Investor directory Bengaluru</title></head><body><main><p>Electronic City Bengaluru software startup investor and funding directory with verified company and venture portfolio information.</p><a href='/portfolio'>Investor portfolio and software companies</a><a href='/privacy'>Privacy</a></main></body></html>""",
        "https://example.com/portfolio": """<html><head><title>Software investor portfolio Bengaluru</title></head><body><main><h1>Portfolio</h1><p>This page lists software and technology startups backed by the investor. For each company, verify stage, sector and official portfolio evidence before treating the firm as an eligible target in Bengaluru. Electronic City is a local technology hub; this directory is an investor research starting point, not proof of individual fund availability.</p><a href='/companies'>Portfolio companies directory</a></main></body></html>""",
        "https://example.com/companies": """<html><head><title>Portfolio companies software startups</title></head><body><main><p>Portfolio companies directory for software and technology startups, with investor portfolio details, founder profiles, and published funding-stage information to validate. This evidence should be cross checked with each official publisher page.</p></main></body></html>""",
    }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, url, **kwargs):
            return FakeResponse(url, pages.get(url, ""))

    monkeypatch.setattr(site_crawler.httpx, "AsyncClient", FakeClient)
    seed = {
        "title": "Investor directory Bengaluru",
        "url": "https://example.com/investors",
        "snippet": "Electronic City Bengaluru software startup investor funding directory",
        "pageContent": "Electronic City Bengaluru software startup investor funding directory with verified company and venture portfolio information.",
        "pageFetchStatus": "ok",
        "pageFetchRelevanceMatched": True,
        "pageFetchUrl": "https://example.com/investors",
    }
    rows, stats = asyncio.run(site_crawler.crawl_relevant_internal_pages(
        [seed], "Electronic City Bengaluru investors for software startups", report_tier="detailed", max_pages=3, max_depth=2
    ))
    assert stats["seed_count"] == 1
    assert stats["pages_requested"] >= 2
    assert rows
    assert all(row["evidenceOrigin"] == "publisher_page_crawl" for row in rows)
    assert all(row["crawlSeedUrl"] == "https://example.com/investors" for row in rows)


def test_planner_repairs_bad_model_shape_and_enforces_investor_coverage(monkeypatch):
    calls = []

    async def fake_plan_call(system, prompt, **kwargs):
        calls.append(kwargs.get("purpose"))
        if len(calls) == 1:
            return {"depth": "detailed", "outline": ["malformed section"]}
        return {
            "depth": "detailed",
            "sections": [{
                "heading": "Investor options",
                "purpose": "Compare named candidates only where sources establish relevance.",
                "format": "mixed",
            }],
        }

    monkeypatch.setattr(report, "_call_llm_json", fake_plan_call)
    intent = SimpleNamespace(
        resolved_topic="investors for software projects in Electronic City, Bengaluru",
        evidence_needed=["general_knowledge"],
        intent_label="local investor discovery",
        source="test",
    )
    sources = [{
        "title": "Electronic City investor and startup directory",
        "url": "https://example.com/bengaluru-investors",
        "snippet": "Software startups, venture capital and investor portfolios in Electronic City, Bengaluru, India.",
        "pageContent": "Software startups, venture capital, investors, portfolio companies and local funding networks in Electronic City, Bengaluru, India.",
    }]
    plan = asyncio.run(report._plan_report_structure(
        question="investors for software projects in Electronic City, Bengaluru",
        enriched_sources=sources,
        file_section="",
        okf_context="",
        no_web_sources=False,
        conversation_context="",
        wants_more_data_viz=False,
        intent=intent,
        report_tier="detailed",
    ))
    assert len(calls) == 2
    assert calls[0] == "report planner"
    assert calls[1] == "report planner repair"
    assert report._validate_report_plan(plan)
    assert len(plan["sections"]) >= 4
    assert plan["tier"] == "detailed"
