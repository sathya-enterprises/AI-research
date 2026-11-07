from routes.report import _expand_report_to_tier
from utils.intent import _extract_research_topic


def test_depth_words_do_not_become_research_topic():
    topic = _extract_research_topic("Create a Brief research report on the global GLP-1 obesity and diabetes drug market.")
    assert "detailed" not in topic.lower()
    assert "report" not in topic.lower()
    assert "glp-1" in topic.lower()


def test_automatic_generation_does_not_expand_to_a_length_quota_for_any_tier():
    original = "## Market view\n\nA concise evidence-backed draft."
    import asyncio
    for tier in ("detailed", "in_depth"):
        result = asyncio.run(_expand_report_to_tier(original, "GLP-1 market", tier, None, "evidence"))
        assert " ".join(result.split()) == " ".join(original.split())


def test_page_budget_measures_without_expanding_under_a_former_page_floor(monkeypatch):
    import asyncio
    from routes import report as report_mod

    preflight_results = iter([4])

    async def expand(report_text, question, report_tier, report_plan, evidence_text, target_override=None, max_passes_override=None):
        raise AssertionError("pagination measurement must not invoke prose expansion")

    monkeypatch.setattr(report_mod, "_rendered_page_count_preflight", lambda *args, **kwargs: next(preflight_results))
    monkeypatch.setattr(report_mod, "_expand_report_to_tier", expand)
    original = "## Market View\n\n" + ("Source-grounded analysis of distinct market drivers. " * 50)
    body, pages = asyncio.run(report_mod._ensure_report_page_budget(
        report_text=original, question="Analyze the market", report_tier="detailed",
        report_plan={"sections": [{"heading": "Market View"}]}, evidence_text="Verified sources",
        title="Test", summary="Summary", key_stats=[], charts=[], sources=[], presentation={},
    ))
    assert pages == 4
    assert body == original.rstrip()


def test_fallback_writer_has_no_page_or_length_quota():
    from routes import report as report_mod

    fallback = report_mod._REPORT_FALLBACK_SYSTEM.lower()
    assert "do not target a page, word, character, section, statistic, table, or visualization count" in fallback
    assert "70-100 page target" not in fallback
    assert "until the evidence has been substantively exhausted" not in fallback



def test_primary_writer_has_no_page_or_length_floor_and_no_numeric_example_values():
    from routes import report as report_mod

    prompt = report_mod.SYSTEM_PROMPT.lower()
    assert "length floor" not in prompt
    assert "minimum length below" not in prompt
    assert "page targets are approximate rendered-document targets" not in prompt
    assert "word-count target" not in prompt
    # Chart examples must not seed realistic-looking figures that could be
    # accidentally copied into a report without source support.
    assert "values=[15.9, 15.8, 35.2, 28.4]" not in prompt
    assert "values=[61,53,32,32,18]" not in prompt


def test_legacy_character_floor_helper_does_not_pad_reports(monkeypatch):
    import asyncio
    from routes import report as report_mod

    async def forbidden_provider(*args, **kwargs):
        raise AssertionError("length floor helper must not call a model")

    monkeypatch.setattr(report_mod, "get_gemini_keys", forbidden_provider)
    original = "## Finding\n\nOne short evidence-backed finding."
    result = asyncio.run(report_mod._extend_report_to_floor(original, "question", 100_000))
    assert result == original


def test_active_report_card_exposes_the_supported_pdf_export():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    active = (root / "frontend" / "src" / "components" / "GrowthGradualChat.tsx").read_text(encoding="utf-8")
    assert 'onClick={downloadPdf}' in active
    assert "fetch('/api/chat/report/pdf'" in active


def test_automated_export_capture_intercepts_native_save_without_mutating_session():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    helper = root / "frontend" / "scripts" / "capture-report-export.cjs"
    text = helper.read_text(encoding="utf-8")
    assert "HTMLAnchorElement.prototype.click" in text
    assert "window.__ggQa" not in text  # no global session persistence hooks
    assert "localStorage" not in text
    assert "originalBrowserSessionPreserved: true" in text
    assert "saveAsDialogAvoided: true" in text
