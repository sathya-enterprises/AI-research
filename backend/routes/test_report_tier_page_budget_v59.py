from __future__ import annotations

import asyncio
import json

from routes import report
from utils import site_crawler


def test_json_wrapped_continuation_sections_are_recovered():
    raw = json.dumps({"sections": [{
        "heading": "Risk assessment",
        "content": "### Financing risk\n\nThe source-backed evidence identifies three separate risk channels and their practical implications [S1].",
    }]})
    assert report._parse_continuation_sections(raw) == [(
        "Risk assessment",
        "### Financing risk\n\nThe source-backed evidence identifies three separate risk channels and their practical implications [S1].",
    )]


def test_page_budget_expands_until_final_renderer_reaches_tier_target(monkeypatch):
    measured_pages = iter([3, 12, 27])
    expansion_calls = []

    def fake_preflight(*args, **kwargs):
        return next(measured_pages, 27)

    async def fake_expand(**kwargs):
        round_no = len(expansion_calls) + 1
        expansion_calls.append(kwargs)
        modules = []
        for item in range(70):
            modules.append(
                f"### Distinct analysis round {round_no}, evidence angle {item + 1}\n\n"
                f"This module evaluates a separate mechanism, comparison, limitation, and decision implication "
                f"for evidence angle {item + 1} in round {round_no}, grounded in source [S1]."
            )
        return kwargs["report_text"] + "\n\n" + "\n\n".join(modules)

    monkeypatch.setattr(report, "_rendered_page_count_preflight", fake_preflight)
    monkeypatch.setattr(report, "_expand_report_to_tier", fake_expand)
    body = "## Findings\n\n" + ("The retrieved sources establish a relevant baseline and identify limitations. [S1] " * 55)
    evidence = "\n\n".join(f"[S{i}] Distinct relevant publisher evidence about the research question." for i in range(1, 25))

    final_body, pages = asyncio.run(report._ensure_report_page_budget(
        report_text=body,
        question="A research topic supported by multiple sources",
        report_tier="detailed",
        report_plan={"sections": [{"heading": "Findings"}]},
        evidence_text=evidence,
        title="Research report",
        summary="Evidence-backed summary",
        key_stats=[], charts=[], sources=[{"id": f"S{i}"} for i in range(1, 25)],
        presentation={},
    ))

    assert len(expansion_calls) == 2
    assert pages == 27
    assert len(final_body) > len(body) + 10_000
    assert all(call["target_override"] > len(call["report_text"]) for call in expansion_calls)
    assert all(call["time_budget_override"] > 0 for call in expansion_calls)


def test_page_budget_does_not_fabricate_content_when_evidence_is_insufficient(monkeypatch):
    called = []

    def fake_preflight(*args, **kwargs):
        return 3

    async def fake_expand(**kwargs):
        called.append(True)
        return kwargs["report_text"] + " fabricated padding"

    monkeypatch.setattr(report, "_rendered_page_count_preflight", fake_preflight)
    monkeypatch.setattr(report, "_expand_report_to_tier", fake_expand)
    body = "## Findings\n\nOnly a small amount of evidence is available."
    final_body, pages = asyncio.run(report._ensure_report_page_budget(
        report_text=body, question="A topic", report_tier="detailed",
        report_plan={"sections": [{"heading": "Findings"}]}, evidence_text="",
        title="Research report", summary="", key_stats=[], charts=[], sources=[], presentation={},
    ))
    assert final_body == body
    assert pages == 3
    assert not called


def test_semantic_completion_recovers_markerless_markdown(monkeypatch):
    base = "## Summary\n\nThe report answers the core question and cites the baseline evidence [S1]."
    plan = {"sections": [
        {"heading": "Summary", "purpose": "Summarize the evidence"},
        {"heading": "Market mechanisms", "purpose": "Explain key mechanisms"},
        {"heading": "Risks and limitations", "purpose": "Explain risks"},
        {"heading": "Implementation priorities", "purpose": "Set priorities"},
    ]}
    calls = []

    async def fake_continuation(prompt, tier):
        calls.append(prompt)
        return (
            f"### Evidence angle {len(calls)}\n\n"
            f"This source-backed passage adds a distinct analytical implication {len(calls)}. "
            "It explains a mechanism, gives the limitation, and identifies what a decision-maker should verify next [S1]. "
            "The evidence does not establish details beyond the documented findings, so no unsupported value is inferred. " * 3,
            "mock-markerless-continuation",
        )

    monkeypatch.setattr(report, "_call_gemini_plain_continuation", fake_continuation)
    evidence = "\n\n".join(
        f"[S{i}] Relevant source evidence supports a specific mechanism and decision implication {i}." for i in range(1, 20)
    )
    out = asyncio.run(report._expand_report_semantically(
        base, "test topic", "detailed", plan, evidence
    ))
    assert len(calls) >= 2
    assert "## Market mechanisms" in out
    assert "## Risks and limitations" in out or "## Implementation priorities" in out
    assert "[[SECTION:" not in out


def test_main_writer_system_prompt_does_not_contradict_tier_page_bands():
    assert "Brief targets 6–10 pages" in report.SYSTEM_PROMPT
    assert "In-Detail targets 25–30 pages" in report.SYSTEM_PROMPT
    assert "Deep Research targets 70–100 pages" in report.SYSTEM_PROMPT
    assert "Page count, word count, character count, section count and visual count are never goals." not in report.SYSTEM_PROMPT


def test_deep_research_crawler_budget_allows_more_pages_and_depth():
    rows, stats = asyncio.run(site_crawler.crawl_relevant_internal_pages(
        [], "a research topic", report_tier="in_depth", max_pages=24, max_depth=3
    ))
    assert rows == []
    assert stats["page_limit"] == 24
    assert stats["max_depth"] == 3
    assert stats["seed_count"] == 0
