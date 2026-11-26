"""Report length accounting: per-section budget, models and extension; status against the mode's page band."""
import asyncio
from types import SimpleNamespace

from utils.page_policy import length_report


def _sec(i, words, budget, ok=True, models=("gemini-3-flash-preview",)):
    return {"id": f"s{i}", "words": words, "budget": budget, "ok": ok, "models": list(models)}


def test_status_within_band_and_above_cap():
    assert length_report("detailed", 27.0, 12000, [_sec(1, 900, 1000)])["status"] == "within"
    assert length_report("detailed", 40.0, 20000, [_sec(1, 900, 1000)])["status"] == "above_cap"


def test_short_report_is_attributed_to_the_evidence_or_to_the_writing():
    # the evidence supports only ~13 pages and every section met its budget: disclosed, not padded
    thin = length_report("detailed", 14.0, 5000, [_sec(1, 900, 1000), _sec(2, 800, 900)])
    assert thin["status"] == "below_band_evidence" and not thin["shortSections"]
    # the evidence supported 30 pages but sections came back short (e.g. a fallback model's short answer)
    short = length_report("detailed", 18.0, 11500, [_sec(1, 300, 1000, models=("groq-model",)), _sec(2, 950, 1000),
                                                   _sec(3, 0, 1000, ok=False, models=())])
    assert short["status"] == "below_band_writing"
    assert short["shortSections"] == ["s1"] and short["failedSections"] == ["s3"]
    assert short["partsByModel"] == {"groq-model": 1, "gemini-3-flash-preview": 1}
    assert short["plannedWords"] == 3000 and short["writtenWords"] == 1250


def test_writer_records_budget_models_and_extension(monkeypatch):
    from utils import section_writer as W

    passages = [SimpleNamespace(passage_id=f"S{i}.1", source_id=f"S{i}", text=f"Fact number {i} about exports.",
                                reliability=0.8, publisher="P", title="T", published="2026-01-01",
                                authority_tier="official", kind="text") for i in range(1, 6)]
    store = SimpleNamespace(get=lambda pid: next((p for p in passages if p.passage_id == pid), None), passages=passages)
    section = SimpleNamespace(section_id="s1", title="Exports", goal="g", word_budget=1000, subsections=[],
                              passage_ids=[p.passage_id for p in passages], leaves=lambda: [])
    calls = []

    class Gateway:
        async def generate(self, **kwargs):
            calls.append(kwargs["purpose"])
            model = "gemini-x" if kwargs["purpose"] == "section" else "gemini-y"
            body = ("Exports grew steadily over the period [S1.1]. " * 30) if kwargs["purpose"] == "section" else \
                   ("Further exports detail matters here [S4.1]. " * 30)
            return SimpleNamespace(ok=True, text=body + "\n<<<SUMMARY>>>\nok", model=model)

    monkeypatch.setattr(W, "_store_fact_sheet", lambda store: None)
    draft = asyncio.run(W.write_section(Gateway(), store, section, [], question="q", thesis="t",
                                        already_covered=[], other_sections=[]))
    assert draft.budget == 1000 and draft.models[0] == "gemini-x"
    assert calls == ["section", "section_extend"] and draft.extended and draft.models == ["gemini-x", "gemini-y"]


def test_sections_are_written_in_calls_gemini_can_fill(monkeypatch):
    from utils.section_writer import _chunks
    subs = [SimpleNamespace(word_budget=1250), SimpleNamespace(word_budget=900), SimpleNamespace(word_budget=400)]
    monkeypatch.delenv("GG_V2_SECTION_CALL_WORDS", raising=False)
    assert [len(g) for g in _chunks(subs)] == [1, 2]          # 1,250 | 900 + 400 (no call asks for 2,550)
    monkeypatch.setenv("GG_V2_SECTION_CALL_WORDS", "2600")
    assert [len(g) for g in _chunks(subs)] == [2, 1]          # the previous behaviour
