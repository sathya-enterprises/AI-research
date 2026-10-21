"""Offline test for sectoral index-history expansion."""
from __future__ import annotations
import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import market_data


def test_sector_history_fetches_all_supported_symbols_concurrently(monkeypatch):
    seen = []
    async def fake_history(client, symbol, range_str, interval):
        seen.append((symbol, range_str, interval))
        return [{"date": "Jan 2025", "close": 100.0}, {"date": "Apr 2025", "close": 105.0}]
    monkeypatch.setattr(market_data, "_yf_chart_history", fake_history)
    result = asyncio.run(market_data.fetch_historical_sector_index_quotes(range_str="3y", interval="3mo", concurrency=4))
    assert len(result) == len(market_data.SECTOR_INDEX_SYMBOLS)
    assert len(seen) == len(market_data.SECTOR_INDEX_SYMBOLS)
    assert all(r == "3y" and i == "3mo" for _, r, i in seen)
    labels = {item["label"] for item in result}
    assert {"NIFTY AUTO", "NIFTY FMCG", "NIFTY METAL", "NIFTY PHARMA", "NIFTY REALTY"}.issubset(labels)
