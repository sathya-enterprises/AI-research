"""Regression tests for continuation stability and Deep Research PDF opening layout."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from routes.report import _dedupe_repeated_subsections, _select_expansion_evidence
from routes.html_report import build_html_report, _render_key_stats


def _presentation():
    return {
        "domain": "finance",
        "cover": {"enabled": True, "treatment": "minimal", "show_date": True},
        "executive_summary": {"placement": "after_cover", "heading": "Executive Summary", "key_metrics": []},
        "sections": [
            {"id": "s1", "title": "Executive Summary & Macroeconomic Context", "section_type": "overview", "layout": "single_column", "density": "standard", "emphasis": "normal", "order": 0, "blocks": []},
            {"id": "s2", "title": "Financial Performance", "section_type": "financials", "layout": "single_column", "density": "standard", "emphasis": "normal", "order": 1, "blocks": []},
        ],
        "source_appendix": {"placement": "end_of_report", "include_appendix": True},
        "default_layout": "single_column",
        "default_density": "standard",
        "visual_spec": {"composition_mode": "narrative_led", "title_treatment": "minimal", "section_treatment": "ruled", "chart_treatment": "framed", "table_treatment": "striped", "background_style": "plain", "visual_rhythm": "narrative_flow", "whitespace_profile": "balanced", "emphasis_profile": "narrative", "density": "balanced", "opening_mode": "narrative"},
    }


def test_qualified_executive_summary_does_not_create_duplicate_summary_page():
    report = (
        "## Executive Summary & Macroeconomic Context\n\n"
        "The sector enters the forecast period with sound capital buffers and sustained credit demand [S1].\n\n"
        "## Financial Performance\n\nProfitability is supported by fee income [S2]."
    )
    html = build_html_report(report, "Banking Outlook", "Banking", "A compact duplicated summary.", [], [], [], None, [], presentation=_presentation(), report_tier="in_depth")
    assert 'id="report-overview"' not in html
    assert html.count('class="gg-summary-heading"') == 0
    assert 'data-section-number="01"' in html


def test_repeated_subsection_across_top_level_sections_is_removed_when_anchors_are_old():
    repeated_1 = (
        "Credit grew 16.08% in FY26 while deposits grew 13.47%, increasing funding pressure on banks [S1] [S7]. "
        "Banks responded with targeted term deposits and tighter liquidity management to protect net interest margins. "
        "The same divergence constrained balance-sheet flexibility across major lenders."
    )
    repeated_2 = (
        "The 16.08% credit expansion exceeded 13.47% deposit growth in FY26, intensifying the funding squeeze [S1] [S7]. "
        "Targeted deposit pricing and closer liquidity management helped lenders protect net interest margins. "
        "This imbalance continued to constrain balance-sheet flexibility for major banks."
    )
    report = (
        "## Credit and Deposits\n\n### Credit-Deposit Divergence\n\n" + repeated_1 + "\n\n"
        "## Profitability\n\n### Funding Asymmetry\n\n" + repeated_2
    )
    cleaned = _dedupe_repeated_subsections(report)
    assert "### Credit-Deposit Divergence" in cleaned
    assert "### Funding Asymmetry" not in cleaned


def test_subsection_with_new_quantitative_evidence_is_preserved():
    first = (
        "Credit grew 16.08% in FY26 while deposits grew 13.47%, increasing funding pressure on banks [S1] [S7]. "
        "Banks responded with targeted term deposits and tighter liquidity management to protect net interest margins."
    )
    second = (
        "Credit growth of 16.08% exceeded deposit growth of 13.47%, creating funding pressure [S1] [S7]. "
        "This dynamic matters for liquidity planning, while the credit-deposit ratio also reached 81.2% in a later period [S9]."
    )
    report = "## Credit\n\n### Credit-Deposit Divergence\n\n" + first + "\n\n## Risk\n\n### Funding Pressure\n\n" + second
    cleaned = _dedupe_repeated_subsections(report)
    assert "### Funding Pressure" in cleaned
    assert "81.2%" in cleaned


def test_expansion_evidence_selection_keeps_section_relevant_later_passage():
    evidence = (
        "Company history and unrelated segment results with broad market information.\n\n"
        "Risk evidence: MSME delinquency grew after a regional supply-chain shock; scenario sensitivity and provisioning coverage are discussed here [S17].\n\n"
        "An unrelated description of digital onboarding systems and account opening."
    )
    selected = _select_expansion_evidence(evidence, "India banking outlook", ["MSME credit risk", "Provisioning coverage"], max_chars=180)
    assert "MSME delinquency" in selected or "provisioning coverage" in selected.casefold()
    assert len(selected) <= 180


def test_identical_kpi_change_is_not_rendered_twice():
    html = _render_key_stats([{"label": "Credit growth", "value": "15.9%", "change": "+15.9%"}], [], 3)
    assert "+15.9%" not in html
    assert 'class="gg-stat-change' not in html
    assert 'data-count-target="15.9"' in html


def test_windows_pdf_rendering_prefers_installed_chromium_over_sync_playwright(monkeypatch):
    from routes import pdf
    calls = []
    monkeypatch.setattr(pdf.os, "name", "nt")
    monkeypatch.setattr(pdf, "_pdf_with_chromium", lambda html: calls.append("chromium") or b"%PDF-test")
    monkeypatch.setattr(pdf, "_pdf_with_playwright", lambda html: (_ for _ in ()).throw(AssertionError("Playwright should be skipped first on Windows")))
    monkeypatch.setattr(pdf, "_pdf_with_weasyprint", lambda html: (_ for _ in ()).throw(AssertionError("WeasyPrint should not be needed")))
    monkeypatch.setattr("routes.html_report.build_html_report", lambda *args, **kwargs: "<html><body>test</body></html>")
    monkeypatch.setattr(pdf, "_replace_chart_runtime_with_svg", lambda html, theme=None: html)
    monkeypatch.setattr(pdf, "_strip_non_printing_runtime", lambda html: html)
    result = pdf.build_pdf("## Findings\n\nEvidence.", "Title", "Question", "", [], [], report_tier="in_depth")
    assert result.startswith(b"%PDF-")
    assert calls == ["chromium"]

@pytest.mark.asyncio
async def test_groq_continuation_retries_413_with_smaller_prompt_and_token_budget(monkeypatch):
    from routes import report as report_module

    requests = []

    class FakeResponse:
        def __init__(self, status_code, content=""):
            self.status_code = status_code
            self.is_success = 200 <= status_code < 300
            self._content = content

        def json(self):
            return {"choices": [{"message": {"content": self._content}}]}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url, *, headers, json):
            requests.append(json)
            return FakeResponse(413) if len(requests) == 1 else FakeResponse(200, "New evidence-backed section.")

    monkeypatch.setattr(report_module, "get_groq_keys", lambda: ["test-key-1234"])
    monkeypatch.setattr(report_module, "round_robin", lambda values: list(values))
    monkeypatch.setattr(report_module, "is_rate_limited", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(report_module, "mark_rate_limited", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(report_module.httpx, "AsyncClient", FakeAsyncClient)

    prompt = "Task and section headings.\n" + ("middle coverage text " * 1600) + "\n\nSelected evidence: latest risk and liquidity figures [S5]."
    content, model = await report_module._call_groq_plain_continuation(prompt, "in_depth")

    assert content == "New evidence-backed section."
    assert model == "groq:gpt-oss-120b:continuation"
    assert len(requests) == 2
    first_user = requests[0]["messages"][1]["content"]
    second_user = requests[1]["messages"][1]["content"]
    assert len(second_user) < len(first_user)
    assert requests[0]["max_tokens"] == 6000
    assert requests[1]["max_tokens"] == 3500
    assert "Selected evidence" in second_user
