"""Index change over a short requested window, calculated from daily closes (no network in these tests)."""
from utils.evidence_provenance import is_trusted_calculated_sector_source
from utils.market_data import _window_change, format_index_window_returns_as_source, requested_window_days


def test_requested_window_reads_days_weeks_and_months_but_not_long_horizons():
    assert requested_window_days("how the Indian share market performed over the last one month") == 30
    assert requested_window_days("FII flows in the past 2 weeks") == 14
    assert requested_window_days("sector moves over the last 3 months") == 90
    assert requested_window_days("sector rotation in the past 8 quarters") is None
    assert requested_window_days("EV adoption over the past 5 years") is None


def test_window_change_uses_the_first_close_inside_the_window_and_the_latest_close():
    points = [{"date": "2026-10-17", "close": 100.0}, {"date": "2026-10-21", "close": 110.0},
              {"date": "2026-11-10", "close": 115.0}, {"date": "2026-11-20", "close": 121.0}]
    change = _window_change(points, 30)
    assert change["start_date"] == "2026-10-21" and change["end_date"] == "2026-11-20"
    assert round(change["change"], 2) == 10.0


def test_window_returns_source_is_a_trusted_calculated_table_with_its_closes():
    rows = [{"label": "NIFTY 50", "start_date": "2026-10-21", "start_close": 22811.35, "end_date": "2026-11-20",
             "end_close": 25800.60, "change": 13.104},
            {"label": "NIFTY IT", "start_date": "2026-10-21", "start_close": 32422.50, "end_date": "2026-11-20",
             "end_close": 31322.65, "change": -3.392}]
    source = format_index_window_returns_as_source(rows, 30)
    text = source["fullContent"]
    assert "Index | Change over the last 30 days (%)" in text
    assert "NIFTY 50 | +13.10%" in text and "NIFTY IT | -3.39%" in text
    assert "NIFTY 50: 21 Oct 2026 close 22,811.35; 20 Nov 2026 close 25,800.60" in text
    assert is_trusted_calculated_sector_source(source)
    # after the source registry strips internal URLs and renames it, it is still recognised
    assert is_trusted_calculated_sector_source({"title": "Index returns over the requested period (calculated)",
                                                "kind": "Verified data", "publisher": "Calculated from daily market data",
                                                "evidenceOrigin": "calculated_from_daily_market_data",
                                                "chartEvidenceExcerpt": text})
