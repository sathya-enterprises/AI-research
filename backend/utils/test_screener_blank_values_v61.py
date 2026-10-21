"""v61: Screener placeholders ("₹ Cr.", "%", "- close price") are blanks, not data."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, ROOT.as_posix())

from utils import screener_kb as kb

_spec = importlib.util.spec_from_file_location("gg_loader", ROOT / "screener_kb" / "load_screener_data.py")
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)


def test_loader_skips_unit_only_cells():
    ratios = pd.DataFrame({
        "Metric": ["Market Cap", "Stock P/E", "ROE", "High / Low"],
        "Value": ["₹ Cr.", None, "12.4 %", "₹ 86.8 / 27.0"],
        "Numeric Value": [None, None, 12.4, 86.8],
    })
    rows = loader.parse_top_ratios(ratios)
    assert [r["metric"] for r in rows] == ["ROE", "High / Low"]
    growth = pd.DataFrame({"Metric": ["Sales CAGR", "Sales CAGR"], "Period": ["10 Years:", "5 Years:"],
                           "Value": ["%", "39%"], "Numeric Value": [None, 39.0]})
    assert [(g["period"], g["value"]) for g in loader.parse_growth_cagr(growth)] == [("5 Years", 39.0)]
    wide = pd.DataFrame({"Unnamed": ["Sales +", "OPM %"], "2025-03-31": ["%", "12%"], "2026-03-31": [None, "14%"]})
    assert [(r["line_item"], r["period"]) for r in loader.melt_wide_statement(wide, "Annual P&L")] == [
        ("OPM %", "2025-03-31"), ("OPM %", "2026-03-31")]
    info = loader.parse_company_info(pd.DataFrame([{"Ticker": "X", "Company Name": "X Ltd", "Price Date": "- close price"}]))
    assert info["price_date_label"] is None and "Price Date" not in info["raw_info"]


def test_retrieval_drops_blank_rows_orders_periods_and_skips_empty_companies():
    empty = {"company": {"ticker": "VESUVIUS", "name": "Vesuvius", "price_date_label": "- close price"},
             "ratios": [{"metric": "Market Cap", "raw_value": "₹ Cr.", "value": None}],
             "growth_cagr": [{"metric": "Sales", "period": "TTM", "raw_value": "%", "value": None}],
             "financials": [], "shareholding": [], "peers": [], "pros_cons": []}
    assert not kb.snapshot_has_figures(kb.clean_snapshot(empty))
    assert kb.format_screener_snapshots_as_source([empty]) is None
    snap = {"company": {"ticker": "V", "name": "V Ltd"},
            "ratios": [{"metric": "ROE", "raw_value": "12.4 %", "value": 12.4},
                       {"metric": "Market Cap", "raw_value": "₹ Cr.", "value": None}],
            "financials": [], "growth_cagr": [], "peers": [], "pros_cons": [],
            "shareholding": [{"frequency": "Quarterly", "holder_type": "Promoters", "period": p, "raw_value": f"{v}%", "value": v}
                             for p, v in (("Sep 2023", 50.0), ("Jun 2026", 55.0), ("Mar 2024", 51.0))]}
    text = kb.format_snapshot_as_context(snap)
    assert "₹ Cr." not in text and "Market Cap" not in text
    assert "Shareholding pattern (Jun 2026)" in text
