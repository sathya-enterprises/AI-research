"""Stage A: source authority, recency, reliability, denylist, near-duplicates, boilerplate."""
from datetime import date

import pytest

from utils import source_quality as Q

TODAY = date(2026, 11, 7)
QUESTION = "Sector rotation in the Indian stock markets over the last 4 quarters"


@pytest.mark.parametrize("url,tier", [
    ("https://www.nseindia.com/market-data/sectoral", "primary_official"),
    ("https://fpi.nsdl.co.in/web/Reports/Latest.aspx", "primary_official"),
    ("https://dea.gov.in/x", "primary_official"),
    ("https://ftp.motilaloswal.com/emailer/Research/x.pdf", "institutional_research"),
    ("https://economictimes.indiatimes.com/markets/x", "established_media"),
    ("https://trendlyne.com/equity/x", "data_aggregator"),
    ("https://ramakrishnavadlamudi.blogspot.com/2025/12/x.html", "low_quality"),
    ("https://ijrpr.com/uploads/V6ISSUE10/x.pdf", "low_quality"),
    ("https://downstox.com/blog/x", "denylisted"),
    ("https://strota.in/sector-flow", "unknown"),
])
def test_authority_tiers(url, tier):
    assert Q.authority(url)[0] == tier


def test_lookalike_brand_detection_without_flagging_the_real_brand():
    assert Q.lookalike_brand("zerodhaa.com") == "zerodha"
    assert Q.lookalike_brand("upstox.com") is None
    assert Q.lookalike_brand("nsearchives.nseindia.com") is None
    assert Q.authority("https://groww.in/x")[0] == "data_aggregator"


def test_env_extends_denylist_and_low_quality(monkeypatch):
    monkeypatch.setenv("GG_SOURCE_DENYLIST", "spam.example=content farm")
    monkeypatch.setenv("GG_SOURCE_LOW_QUALITY", "weak.example")
    assert Q.authority("https://spam.example/a") == ("denylisted", "content farm")
    assert Q.authority("https://weak.example/a")[0] == "low_quality"


def test_recency_relative_to_requested_window():
    start = Q.window_start(QUESTION, TODAY)
    assert start == date(2025, 10, 1)  # Q3 FY26 starts the "last 4 quarters" window
    assert Q.recency({"published": "2026-02-10T00:00:00Z"}, start, TODAY)[0] == 1.0
    old, reason = Q.recency({"publication_date": "2023-10-01"}, start, TODAY)
    assert old == 0.25 and "2.0 years before" in reason  # one-year half-life
    assert Q.recency({}, start, TODAY) == (0.5, "undated")


def test_assess_sources_scores_drops_denylisted_and_copies_search_dates():
    page = "Sector | Return\n" + "\n".join(f"NIFTY S{i} | {i}.{i}%" for i in range(60)) + "\nTABLE 1"
    rows = [
        {"url": "https://www.nseindia.com/a", "pageFetchStatus": "ok", "pageContent": page, "published": "2026-06-30"},
        {"url": "https://downstox.com/b", "pageFetchStatus": "ok", "pageContent": "x"},
        {"url": "https://someblog.blogspot.com/c", "snippet": "sector rotation view", "published": "2022-01-01"},
        {"url": "", "title": "NSE sector returns by calendar quarter (calculated)"},
    ]
    kept, stats = Q.assess_sources(rows, question=QUESTION, today=TODAY)
    assert [r["url"] for r in kept] == ["https://www.nseindia.com/a", "https://someblog.blogspot.com/c", ""]
    assert stats["denylisted"] == 1 and "downstox.com" in stats["denylisted_hosts"][0]
    official, blog, calculated = kept
    assert official["authorityTier"] == "primary_official" and official["reliabilityScore"] > 0.9
    assert blog["authorityTier"] == "low_quality" and blog["reliabilityScore"] < 0.4
    assert "search-index excerpt only" in blog["reliabilityNote"]
    assert official["publication_date"] == "2026-06-30"  # copied from the search provider's "published"
    assert calculated["authorityTier"] == "primary_official" and "calculated" in calculated["reliabilityNote"]


def test_near_duplicates_keep_the_more_reliable_copy():
    text = " ".join(f"word{i}" for i in range(400))
    rows = [
        {"url": "https://unknown-mirror.example/a", "pageFetchStatus": "ok", "pageContent": text},
        {"url": "https://economictimes.indiatimes.com/a", "pageFetchStatus": "ok", "pageContent": text + " extra tail"},
    ]
    kept, stats = Q.assess_sources(rows, question=QUESTION, today=TODAY)
    assert stats["near_duplicates"] == 1
    assert [r["url"] for r in kept] == ["https://economictimes.indiatimes.com/a"]


def test_cross_page_boilerplate_lines_are_removed_but_data_lines_kept():
    nav = "Home | Markets | Subscribe to our newsletter today"
    rows = [{"url": f"https://site.example/p{i}", "pageContent": f"{nav}\nUnique analysis paragraph number {i} about rotation.\nNIFTY IT 12.5% 13.1%"}
            for i in range(3)]
    removed = Q.strip_cross_page_boilerplate(rows)
    assert removed == 3
    assert all(nav not in r["pageContent"] for r in rows)
    assert all("NIFTY IT 12.5% 13.1%" in r["pageContent"] for r in rows)  # repeated numeric lines are data, not navigation


def test_manifest_keeps_search_dates_and_reliability_fields():
    from routes.source_manifest import build_source_registry
    registry = build_source_registry([{
        "title": "Sector rotation tracker", "url": "https://economictimes.indiatimes.com/x", "published": "2026-09-30T00:00:00Z",
        "authorityTier": "established_media", "reliabilityScore": 0.8712, "recencyScore": 1.0,
        "reliabilityNote": "established media; dated 2026-09-30 (inside the requested window)",
    }])
    record = registry[0]
    assert record["publication_date"].startswith("2026-09-30")
    assert record["authority_tier"] == "established_media"
    assert record["reliability_score"] == 0.871 and record["recency_score"] == 1.0
    assert "inside the requested window" in record["reliability_note"]
