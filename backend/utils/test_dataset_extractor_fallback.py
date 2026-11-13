from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from utils import dataset_extractor as extractor


class _Passage:
    passage_id = "S4.1"
    source_id = "S4"
    title = "Official solar statistics"
    publisher = "Official statistics"
    kind = "narrative"

    def __init__(self, text: str):
        self.text = text


class _Store:
    def __init__(self, passage):
        self.passage = passage

    def search(self, *_args, **_kwargs):
        return [(self.passage, 1.0)]

    def get(self, passage_id):
        return self.passage if passage_id == self.passage.passage_id else None


class _EmptyGateway:
    async def generate(self, **_kwargs):
        return SimpleNamespace(ok=True, json=lambda: {"datasets": []})


def test_empty_model_proposal_falls_back_to_source_aligned_year_value_rows(monkeypatch):
    passage = _Passage(
        "Annual solar generation (TWh)\n"
        "Period | Value\n"
        "2022 | 4.0 TWh\n"
        "2023 | 5.5 TWh\n"
        "2024 | 7.0 TWh"
    )
    monkeypatch.setattr(extractor, "candidate_passages", lambda *_args, **_kwargs: [passage])
    monkeypatch.setattr("utils.source_quality.window_start", lambda _question, _today: date(2022, 1, 1))

    charts, note = asyncio.run(extractor.extract_dataset_charts(
        _Store(passage), _EmptyGateway(), question="Compare solar generation from 2022 to 2024",
        today="2026-11-12", limit=2,
    ))

    assert len(charts) == 1
    assert [point[1] for point in charts[0].points] == ["2022", "2023", "2024"]
    assert [point[2] for point in charts[0].points] == [4.0, 5.5, 7.0]
    assert charts[0].spec["unitLabel"] == "TWh"
    assert note["emptyResponses"] == 1
    assert note["deterministicFallbackAccepted"] == 1


def test_deterministic_fallback_rejects_ranked_or_mixed_unit_rows():
    ranked = _Passage(
        "Annual solar capacity (GW)\nPeriod | Rank | Value\n"
        "2022 | 1 | 4.0\n2023 | 2 | 5.5\n2024 | 3 | 7.0"
    )
    mixed = _Passage(
        "Annual solar capacity\nPeriod | Value\n"
        "2022 | 4.0 GW\n2023 | 5.5 %\n2024 | 7.0 GW"
    )

    assert extractor._deterministic_period_dataset(ranked)[0] is None
    assert extractor._deterministic_period_dataset(mixed)[0] is None
