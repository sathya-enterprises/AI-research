"""The run summary gathers timings, model telemetry, claims, charts and length in one log-safe block."""
import json

from utils.run_summary import StageClock, build_run_summary, summary_line


def test_summary_from_meta():
    meta = {"elapsedSeconds": 612.4, "words": 21000, "estimatedPages": 74.2,
            "llm": {"calls": 48, "succeeded": 45, "failed": 3, "tokensIn": 512000, "tokensOut": 61000,
                    "models": {"gemini-3-flash-preview": 30, "groq:gpt-oss-120b:continuation": 4},
                    "byPurpose": {"section": 14}, "httpStatus": {"429": 7, "503": 2, "200": 45},
                    "networkRetries": 1, "quotaWaits": 2, "quotaWaitSeconds": 31.5, "fallbacks": 4,
                    "groqBeforeWait": 3, "shortFallbacksRetried": 1},
            "sections": [{"ok": True}, {"ok": True}, {"ok": False}],
            "claimCheck": [{"byStatus": {"VERIFIED": 20, "REQUIRES_REVIEW": 3}, "corrected": [{}], "removed": [{}, {}]},
                           {"byStatus": {"VERIFIED": 5, "UNVERIFIED": 1}, "corrected": [], "removed": []}],
            "charts": {"candidates": 120, "planned": 40, "used": 36},
            "citations": {"sourcesCited": 88, "passagesCited": 400},
            "length": {"status": "within", "shortSections": ["s3"], "failedSections": ["s9"]}}
    s = build_run_summary(meta, tier="in_depth", stages={"outline": 20.0, "section_writing": 300.0})
    assert s["llm"]["http429"] == 7 and s["llm"]["http503"] == 2 and s["llm"]["fallbacks"] == 4
    assert s["claims"] == {"byStatus": {"VERIFIED": 25, "REQUIRES_REVIEW": 3, "UNVERIFIED": 1}, "corrected": 1, "removed": 2}
    assert s["sections"] == {"written": 2, "planned": 3, "short": ["s3"], "failed": ["s9"]}
    assert s["charts"]["used"] == 36 and s["sources"]["cited"] == 88 and s["lengthStatus"] == "within"
    line = summary_line(s)
    assert "429x7" in line and "outline 20s" in line and "pages 74.2 (within)" in line
    assert "AIza" not in json.dumps(s)


def test_stage_clock_accumulates_named_stages():
    clock = StageClock()
    clock.mark("a")
    clock.mark("b")
    clock.mark("a")
    assert set(clock.to_dict()) == {"a", "b"} and all(v >= 0 for v in clock.to_dict().values())


def test_empty_meta_does_not_fail():
    s = build_run_summary({}, tier="detailed")
    assert s["llm"]["http429"] == 0 and s["claims"]["byStatus"] == {} and summary_line(s)
