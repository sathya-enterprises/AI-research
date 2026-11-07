import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

from routes import report


def test_renderer_expansion_retries_provider_failure_before_marking_no_progress(monkeypatch):
    prompts = []

    async def continuation(prompt, tier):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "", ""
        body = "### Evidence-backed implications\n\n" + (
            "The source-grounded mechanism changes the interpretation of the result and its practical implications. " * 18
        )
        return f"[[SECTION: Overview]]\n{body}\n[[END SECTION]]", "mock-model"

    monkeypatch.setattr(report, "_call_gemini_plain_continuation", continuation)
    original = "## Overview\n\nA short initial finding supported by [S1]."
    expanded = asyncio.run(report._expand_report_to_tier(
        original,
        "A research question",
        "detailed",
        {"sections": [{"heading": "Overview"}]},
        "[S1] Evidence about the observed change. " * 40,
        target_override=len(original) + 5_000,
        max_passes_override=2,
        time_budget_override=5,
    ))

    assert len(prompts) == 2
    assert "PROVIDER RECOVERY" in prompts[1]
    assert len(expanded) > len(original) + 800
    assert "Evidence-backed implications" in expanded


def test_semantic_completion_retries_provider_failure_on_same_planned_section(monkeypatch):
    prompts = []

    async def continuation(prompt, tier):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "", ""
        body = "The evidence establishes a material difference between the observed cases. " * 8
        return f"[[SECTION: Missing Lens]]\n{body}\n[[END SECTION]]", "mock-model"

    monkeypatch.setattr(report, "_call_gemini_plain_continuation", continuation)
    monkeypatch.setitem(report._TIER_EXPANSION_PASSES, "detailed", 2)
    original = "## Overview\n\nInitial discussion supported by the available source [S1]."
    expanded = asyncio.run(report._expand_report_to_tier(
        original,
        "A research question",
        "detailed",
        {"sections": [{"heading": "Overview"}, {"heading": "Missing Lens"}]},
        "[S1] Evidence about the observed change. " * 40,
    ))

    assert len(prompts) == 2
    assert "RECOVERY ATTEMPT" in prompts[1]
    assert "## Missing Lens" in expanded
    assert "material difference" in expanded


def test_renderer_expansion_rotates_after_no_evidence_instead_of_ending_early(monkeypatch):
    prompts = []
    headings = [f"Section {index}" for index in range(1, 10)]
    original = "\n\n".join(
        f"## {heading}\n\nA concise baseline finding supported by [S1]."
        for heading in headings
    )

    async def continuation(prompt, tier):
        prompts.append(prompt)
        if len(prompts) <= 2:
            return "NO_ADDITIONAL_EVIDENCE", "mock-model"
        selected = re.findall(r"\[\[SECTION TO EXPAND\]\]\s*(.+)", prompt)
        body = "### Additional evidence\n\n" + (
            "The supported comparison adds a distinct mechanism and a practical implication for this section. " * 22
        )
        return "\n".join(
            f"[[SECTION: {heading}]]\n{body} {heading}\n[[END SECTION]]"
            for heading in selected
        ), "mock-model"

    monkeypatch.setattr(report, "_call_gemini_plain_continuation", continuation)
    monkeypatch.setitem(report._TIER_EXPANSION_PASSES, "detailed", 3)
    expanded = asyncio.run(report._expand_report_to_tier(
        original,
        "A research question",
        "detailed",
        {"sections": [{"heading": heading} for heading in headings]},
        "[S1] Evidence about the observed change. " * 40,
        target_override=len(original) + 20_000,
        max_passes_override=3,
        time_budget_override=5,
    ))

    assert len(prompts) == 3
    assert "Section 7" in prompts[2] or "Section 8" in prompts[2] or "Section 9" in prompts[2]
    assert "Additional evidence" in expanded
    assert "NO_ADDITIONAL_EVIDENCE" not in expanded


def test_renderer_expansion_times_out_then_retries_within_remaining_budget(monkeypatch):
    prompts = []

    async def continuation(prompt, tier):
        prompts.append(prompt)
        if len(prompts) == 1:
            await asyncio.sleep(0.1)
            return "", ""
        body = "### Evidence-backed implications\n\n" + (
            "The supported mechanism changes the interpretation of the result and its practical implications. " * 18
        )
        return f"[[SECTION: Overview]]\n{body}\n[[END SECTION]]", "mock-model"

    monkeypatch.setattr(report, "_call_gemini_plain_continuation", continuation)
    monkeypatch.setattr(report, "_TIER_EXPANSION_ATTEMPT_TIMEOUT_SECONDS", 0.01)
    original = "## Overview\n\nA short initial finding supported by [S1]."
    expanded = asyncio.run(report._expand_report_to_tier(
        original,
        "A research question",
        "detailed",
        {"sections": [{"heading": "Overview"}]},
        "[S1] Evidence about the observed change. " * 40,
        target_override=len(original) + 5_000,
        max_passes_override=2,
        time_budget_override=5,
    ))

    assert len(prompts) == 2
    assert "PROVIDER RECOVERY" in prompts[1]
    assert len(expanded) > len(original) + 800


def test_semantic_completion_rotates_to_other_sections_after_no_evidence(monkeypatch):
    prompts = []
    headings = [f"Section {index}" for index in range(1, 5)]
    original = "\n\n".join(
        f"## {heading}\n\nA concise baseline finding supported by [S1]."
        for heading in headings
    )

    async def continuation(prompt, tier):
        prompts.append(prompt)
        return "NO_ADDITIONAL_EVIDENCE", "mock-model"

    monkeypatch.setattr(report, "_call_gemini_plain_continuation", continuation)
    monkeypatch.setitem(report._TIER_EXPANSION_PASSES, "detailed", 2)
    asyncio.run(report._expand_report_to_tier(
        original,
        "A research question",
        "detailed",
        {"sections": [{"heading": heading} for heading in headings]},
        "[S1] Evidence about the observed change. " * 40,
    ))

    assert len(prompts) == 2
    assert "Section 1" in prompts[0] or "Section 2" in prompts[0]
    assert "Section 3" in prompts[1] or "Section 4" in prompts[1]
    assert prompts[0] != prompts[1]
