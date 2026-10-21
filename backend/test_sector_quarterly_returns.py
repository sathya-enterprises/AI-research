"""Regression tests for exact completed-calendar-quarter sector returns."""
import asyncio
from datetime import datetime, timezone

from utils import market_data


def test_quarterly_source_discloses_missing_cells_and_uses_return_units():
    source = market_data.format_sector_quarterly_returns_as_source([
        {"label": "NIFTY AUTO", "quarters": ["Q4 2024", "Q1 2025"],
         "returns": [3.25, None], "missing_quarters": ["Q1 2025"]}
    ])
    assert source is not None
    assert "Q4 2024" in source["fullContent"]
    assert "+3.25%" in source["fullContent"]
    assert "N/A" in source["fullContent"]
    assert "Missing observations" in source["fullContent"]
    assert "internal://verified-sector-quarterly-returns" == source["url"]


def test_fetch_sector_quarterly_returns_uses_month_end_close_series(monkeypatch):
    async def fake_history(client, symbol, range_str, interval):
        assert range_str == "5y"
        assert interval == "1mo"
        # Monthly bars use the month label and the bar close represents the
        # final available trading close in that month.
        return [
            {"date": "Sep 2024", "close": 90.0},
            {"date": "Dec 2024", "close": 100.0},
            {"date": "Mar 2025", "close": 110.0},
            {"date": "Jun 2025", "close": 99.0},
            {"date": "Sep 2025", "close": 108.9},
            {"date": "Dec 2025", "close": 119.79},
            {"date": "Mar 2026", "close": 125.0},
            {"date": "Jun 2026", "close": 130.0},
            {"date": "Sep 2026", "close": 135.0},
        ]
    monkeypatch.setattr(market_data, "_yf_chart_history", fake_history)
    monkeypatch.setattr(market_data, "SECTOR_INDEX_SYMBOLS", [("^TEST", "TEST SECTOR")])
    rows = asyncio.run(market_data.fetch_sector_quarterly_returns(quarters=4, concurrency=1))
    assert len(rows) == 1
    assert rows[0]["label"] == "TEST SECTOR"
    assert len(rows[0]["quarters"]) == 4
    assert len(rows[0]["returns"]) == 4
    assert all(value is None or isinstance(value, float) for value in rows[0]["returns"])


def test_quarterly_chart_survives_canonical_source_registry():
    """Regression: source mapping survives internal URL normalization."""
    from routes.source_manifest import build_source_registry
    from routes.report import _build_sector_quarterly_return_chart

    quarters = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"]
    rows = [
        {"label": "NIFTY AUTO", "quarters": quarters,
         "returns": [1.5, -2.2, 3.4, 4.1, -0.5, 2.2, 1.9, 5.1], "missing_quarters": []},
        {"label": "NIFTY METAL", "quarters": quarters,
         "returns": [0.5, 1.2, -1.4, 2.0, 4.5, -2.1, 3.9, 2.1], "missing_quarters": []},
    ]
    source = market_data.format_sector_quarterly_returns_as_source(rows)
    manifest = build_source_registry([source])
    assert manifest[0]["url"] == ""
    assert "NIFTY AUTO | +1.50%" in manifest[0]["chartEvidenceExcerpt"]

    chart = _build_sector_quarterly_return_chart(rows, manifest)
    assert chart is not None
    assert chart["source"] == [manifest[0]["id"]]
    assert len(chart["series"]) == 2
    assert chart["title"] == "NSE Sector Returns by Completed Calendar Quarter"
    assert chart["xLabel"] == "Completed calendar quarter"
    assert chart["yLabel"] == "Quarterly return (%)"
    assert len(chart["series"][0]["data"]) == 8


def test_sector_quarterly_matrix_is_inserted_under_real_heading():
    from routes.report import _insert_sector_quarterly_return_matrix
    quarters = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"]
    rows = [{"label": "NIFTY AUTO", "quarters": quarters,
             "returns": [1.5, -2.2, 3.4, 4.1, -0.5, 2.2, 1.9, 5.1], "missing_quarters": []}]
    src = market_data.format_sector_quarterly_returns_as_source(rows)
    from routes.source_manifest import build_source_registry
    manifest = build_source_registry([src])
    body = "## Quarterly Sector-Return Matrix\n\nSector rotation over eight quarters.\n\n## Drivers\nAnalysis follows."
    result = _insert_sector_quarterly_return_matrix(body, rows, manifest)
    assert "| Sector index | Q4 2024 | Q1 2025 | Q2 2025 | Q3 2025 | Q4 2025 | Q1 2026 | Q2 2026 | Q3 2026 |" in result
    assert result.index("| Sector index |") < result.index("## Drivers")
    assert "| NIFTY AUTO | +1.50% | -2.20% | +3.40%" in result


def test_official_niftyindices_fallback_recovers_missing_yahoo_ticker(monkeypatch):
    async def no_yahoo(client, symbol, range_str, interval):
        return None

    async def official(client, index_name, start_month, end_month):
        assert index_name == "TEST SECTOR"
        # The selected ends depend on current quarter; return close history for
        # the complete generated range, with steadily changing observed values.
        from datetime import datetime
        from calendar import monthrange
        from datetime import date
        current = date(start_month[0], start_month[1], 1)
        last = date(end_month[0], end_month[1], monthrange(end_month[0], end_month[1])[1])
        out = {}
        i = 1
        while current <= last:
            out[(current.year, current.month)] = 100.0 + i * 5.0
            i += 1
            month = current.month + 1
            year = current.year + (1 if month == 13 else 0)
            current = date(year, 1 if month == 13 else month, 1)
        return out

    monkeypatch.setattr(market_data, "_yf_chart_history", no_yahoo)
    monkeypatch.setattr(market_data, "_niftyindices_month_end_closes", official)
    monkeypatch.setattr(market_data, "SECTOR_INDEX_SYMBOLS", [("^NOYAHOO", "TEST SECTOR")])
    rows = asyncio.run(market_data.fetch_sector_quarterly_returns(quarters=4, concurrency=1))
    assert len(rows) == 1
    assert rows[0]["label"] == "TEST SECTOR"
    assert len(rows[0]["returns"]) == 4
    assert all(value is not None for value in rows[0]["returns"])


def test_official_niftyindices_response_is_parsed_to_last_month_closes():
    import json
    from utils.market_data import _niftyindices_month_end_closes

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"d": json.dumps([
                {"HistoricalDate": "30 Sep 2024", "CLOSE": "100.50"},
                {"HistoricalDate": "01 Sep 2024", "CLOSE": "99.00"},
                {"HistoricalDate": "31 Oct 2024", "CLOSE": "105.25"},
            ])}

    class FakeClient:
        def __init__(self): self.calls = []
        async def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse()

    client = FakeClient()
    result = asyncio.run(_niftyindices_month_end_closes(client, "NIFTY AUTO", (2024, 9), (2024, 10)))
    assert result[(2024, 9)] == 100.50
    assert result[(2024, 10)] == 105.25
    assert len(client.calls) == 1
    assert "getHistoricaldatatabletoString" in client.calls[0][0]


def test_quarterly_heatmap_preserves_missing_values_and_axis_labels():
    from routes.source_manifest import build_source_registry
    from routes.report import _build_sector_quarterly_return_heatmap

    quarters = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]
    rows = [
        {"label": "NIFTY AUTO", "quarters": quarters, "returns": [1.2, -0.8, None, 2.5, 0.4]},
        {"label": "NIFTY METAL", "quarters": quarters, "returns": [2.2, 1.1, -1.3, None, 3.1]},
        {"label": "NIFTY IT", "quarters": quarters, "returns": [-0.2, 0.5, 1.5, -2.1, 0.7]},
    ]
    source = market_data.format_sector_quarterly_returns_as_source(rows)
    manifest = build_source_registry([source])
    chart = _build_sector_quarterly_return_heatmap(rows, manifest)
    assert chart is not None
    assert chart["type"] == "heatmap"
    assert chart["xLabel"] == "Completed calendar quarter"
    assert chart["yLabel"] == "NSE sector index"
    assert chart["values"][0][2] is None
    assert chart["source"] == [manifest[0]["id"]]


def test_incomplete_model_matrix_is_replaced_with_all_calculated_sector_rows():
    from routes.report import _insert_sector_quarterly_return_matrix
    from routes.source_manifest import build_source_registry
    quarters = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"]
    labels = ["NIFTY AUTO", "NIFTY FMCG", "NIFTY IT", "NIFTY BANK", "NIFTY METAL", "NIFTY PHARMA", "NIFTY ENERGY"]
    rows = [{"label": label, "quarters": quarters,
             "returns": [float(i + j + 1) for j in range(8)], "missing_quarters": []}
            for i, label in enumerate(labels)]
    source = market_data.format_sector_quarterly_returns_as_source(rows)
    manifest = build_source_registry([source])
    partial = "## Quarterly Sector Return Matrix\n\nObserved returns are shown below.\n\n"               "| Sector index | " + " | ".join(quarters) + " |\n"               "|---|" + "|".join(["---:"] * 8) + "|\n"               "| NIFTY AUTO | +1% | +2% | +3% | +4% | +5% | +6% | +7% | +8% |\n"               "| NIFTY FMCG | +2% | +3% | +4% | +5% | +6% | +7% | +8% | +9% |\n"               "| NIFTY IT | +3% | +4% | +5% | +6% | +7% | +8% | +9% | +10% |\n"               "| NIFTY BANK | +4% | +5% | +6% | +7% | +8% | +9% | +10% | +11% |\n\n## Drivers\nNarrative."
    result = _insert_sector_quarterly_return_matrix(partial, rows, manifest)
    assert result.count("| Sector index |") == 1
    for label in labels:
        assert f"| {label} |" in result
    assert "## Drivers" in result


def test_compounded_sector_chart_uses_only_complete_series_and_cites_calculated_source():
    from routes.report import _build_sector_compounded_return_chart
    from routes.source_manifest import build_source_registry
    quarters = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"]
    rows = [
        {"label": "NIFTY AUTO", "quarters": quarters, "returns": [1.0] * 8},
        {"label": "NIFTY IT", "quarters": quarters, "returns": [2.0] * 8},
        {"label": "NIFTY BANK", "quarters": quarters, "returns": [-1.0] * 8},
        {"label": "NIFTY FMCG", "quarters": quarters, "returns": [1.0, 1.0, None, 1.0, 1.0, 1.0, 1.0, 1.0]},
    ]
    source = market_data.format_sector_quarterly_returns_as_source(rows)
    manifest = build_source_registry([source])
    assert "compounded return across q4 2024 to q3 2026" in manifest[0]["chartEvidenceExcerpt"].casefold()
    chart = _build_sector_compounded_return_chart(rows, manifest)
    assert chart is not None
    assert chart["type"] == "horizontal_bar"
    assert len(chart["series"][0]["data"]) == 3
    assert chart["source"] == [manifest[0]["id"]]
