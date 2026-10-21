from __future__ import annotations

from pathlib import Path
import asyncio
import re

from routes.report import _TIER_EXPANSION_PASSES, _insert_section_addition

ROOT = Path(__file__).resolve().parents[2]
TSX = ROOT / "frontend" / "src" / "components" / "GrowthGradualChat.tsx"
HTML_REPORT = ROOT / "backend" / "routes" / "html_report.py"


def test_automatic_tier_expansion_is_evidence_driven_not_page_or_character_targeted():
    assert _TIER_EXPANSION_PASSES["brief"] == 0
    assert _TIER_EXPANSION_PASSES["detailed"] > 0
    assert _TIER_EXPANSION_PASSES["in_depth"] > _TIER_EXPANSION_PASSES["detailed"]
    source = (Path(__file__).with_name("report.py")).read_text(encoding="utf-8")
    assert "This is not a request to reach a page count, word count, or character count" in source
    text = (ROOT / "backend" / "routes" / "report.py").read_text(encoding="utf-8")
    assert "_TIER_CONTENT_CHARS" not in text
    assert "Do not target a page, word, or character count." in text
    assert "The 70-100 page target" not in text


def test_section_addition_stays_inside_existing_heading():
    report = "## Market Structure\n\nOriginal analysis.\n\n## Risks\n\nRisk analysis."
    out = _insert_section_addition(report, "Market Structure", "### New Evidence\n\nAdditional analysis.")
    assert "Original analysis.\n\n### New Evidence" in out
    assert out.index("### New Evidence") < out.index("## Risks")
    assert out.count("## Market Structure") == 1


def test_frontend_has_persistent_pre_send_tier_selector():
    text = TSX.read_text(encoding="utf-8")
    assert "selectedReportTier" in text
    assert "composer-report-tier" in text
    assert "selectedReportTier === option.value" in text
    assert re.search(r"generateReport\(\s*\n?\s*botMsg\.id", text)
    assert "selectedReportTier," in text


def test_frontend_explicit_report_uses_selected_tier():
    text = TSX.read_text(encoding="utf-8")
    block_start = text.index("if (_REPORT_INTENT_RE.test(q))")
    block_end = text.index("return;", block_start)
    block = text[block_start:block_end]
    assert "generateReport(" in block
    assert "selectedReportTier" in block


def test_tier_expansion_uses_plain_continuation_contract(monkeypatch):
    import routes.report as report_mod

    base = "## Market Structure\n\nExisting market analysis.\n\n## Risks\n\nExisting risk analysis."

    async def fake_plain(prompt: str, report_tier: str):
        return (
            "[[SECTION: Market Structure]]\n"
            "### Demand Economics\nAdditional source-grounded analysis.\n"
            "### Historical Evolution\nFurther evidence-backed detail.\n"
            "[[END SECTION]]",
            "mock-model",
        )

    monkeypatch.setattr(report_mod, "_call_gemini_plain_continuation", fake_plain)
    out = asyncio.run(report_mod._expand_report_to_tier(
        report_text=base,
        question="Analyze the market",
        report_tier="brief",
        report_plan={"sections": [{"heading": "Market Structure"}, {"heading": "Risks"}]},
        evidence_text="[S1] Market evidence",
    ))
    assert "### Demand Economics" not in out
    assert "### Historical Evolution" not in out
    assert out.count("## Market Structure") == 1


def test_metrics_dashboard_is_single_column_in_print_path():
    text = HTML_REPORT.read_text(encoding="utf-8")
    assert 'if section_type == "metrics_dashboard" or block_kinds & {"metrics", "metrics_dashboard", "kpi_grid", "stats"}:' in text


def test_structured_blocks_preserve_narrative_before_presentation_blocks():
    text = HTML_REPORT.read_text(encoding="utf-8")
    needle = 'body_html = (narrative_html + "\\n" + block_html).strip()'
    assert needle in text


def test_pdf_css_avoids_known_weasyprint_incompatible_features():
    text = HTML_REPORT.read_text(encoding="utf-8")
    forbidden = [
        "text-wrap:",
        "border-inline",
        "padding-inline",
        "color-mix(",
        "overflow-x: clip",
        "word-break: break-word",
    ]
    for token in forbidden:
        assert token not in text, token


def test_picker_tier_is_authoritative_over_prompt_wording():
    source = __import__('pathlib').Path(__file__).with_name('report.py').read_text(encoding='utf-8')
    assert 'The frontend report-depth picker is authoritative' in source
    assert 'explicit request depth overrides picker' not in source


def test_longer_tiers_use_distinct_research_breadth_without_expansion_quotas():
    from routes.report import _TIER_SEARCH_LIMITS, _TIER_EXPANSION_PASSES
    from utils.report_tiers import get_report_tier_spec

    assert _TIER_SEARCH_LIMITS["brief"] < _TIER_SEARCH_LIMITS["detailed"] < _TIER_SEARCH_LIMITS["in_depth"]
    assert get_report_tier_spec("brief").research_breadth == "focused"
    assert get_report_tier_spec("detailed").research_breadth == "expanded"
    assert get_report_tier_spec("in_depth").research_breadth == "comprehensive"
    assert _TIER_EXPANSION_PASSES["brief"] == 0
    assert _TIER_EXPANSION_PASSES["detailed"] > 0
    assert _TIER_EXPANSION_PASSES["in_depth"] > _TIER_EXPANSION_PASSES["detailed"]


def test_frontend_longer_tiers_use_background_jobs_without_changing_picker():
    text = TSX.read_text(encoding="utf-8")
    assert "label: 'Brief'" in text and "label: 'In-Detail'" in text and "label: 'Deep Research'" in text
    assert "/api/chat/report/start" in text
    assert "/api/chat/report/status/" in text
    assert "const useBackgroundReportJob = ['brief', 'detailed', 'in_depth'].includes(reportTier)" in text
