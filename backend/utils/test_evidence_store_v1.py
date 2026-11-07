"""Stage A: passage-level evidence store with ids, provenance and reliability."""
from utils.evidence_store import build_evidence_store

TABLE_PAGE = (
    "[STRUCTURED PUBLISHER TABLE DATA — original rows preserved]\n"
    "TABLE 1 — Sectoral index returns by quarter\n"
    "Sector | Q3 FY26 | Q4 FY26 | Q1 FY27 | Q2 FY27\n"
    + "\n".join(f"NIFTY SECTOR{i} | {i}.1% | -{i}.2% | {i}.3% | {i}.4%" for i in range(1, 70))
    + "\n\n[PUBLISHER PAGE TEXT]\n"
    + "Financial services led the rotation in the September quarter as banks re-rated on credit growth. " * 8
    + "\n\nInformation technology lagged as US tariff uncertainty weighed on deal flow and margins. " * 8
)


def _rows():
    return [
        {"url": "https://www.nseindia.com/sectoral", "title": "NSE sectoral returns", "publisher": "NSE",
         "publication_date": "2026-10-05", "pageFetchStatus": "ok", "pageContent": TABLE_PAGE,
         "authorityTier": "primary_official", "reliabilityScore": 0.95},
        {"url": "https://someblog.blogspot.com/x", "title": "Rotation thoughts", "snippet":
         "Financial services rotation view: banks may lead the next quarter according to this blogger's chart reading. " * 6,
         "authorityTier": "low_quality", "reliabilityScore": 0.3},
        {"url": "https://unlisted.example/y", "title": "Not in manifest", "pageContent": "x" * 2000},
    ]


def _manifest():
    return [{"id": "S1", "url": "https://www.nseindia.com/sectoral", "title": "NSE sectoral returns"},
            {"id": "S2", "url": "https://someblog.blogspot.com/x", "title": "Rotation thoughts"}]


def test_passages_take_canonical_ids_and_skip_uncitable_rows():
    store = build_evidence_store(_rows(), manifest=_manifest())
    assert store.source_ids() == ["S1", "S2"]
    ids = [p.passage_id for p in store.passages]
    assert ids[0] == "S1.1" and all(i.startswith(("S1.", "S2.")) for i in ids)
    assert store.get("s1.1").source_id == "S1"


def test_tables_are_split_with_their_header_repeated():
    store = build_evidence_store(_rows(), manifest=_manifest())
    tables = [p for p in store.passages if p.kind == "table"]
    assert len(tables) >= 2
    assert all("Sector | Q3 FY26 | Q4 FY26" in p.text for p in tables)
    assert all(len(p.text) <= 1500 for p in tables)
    assert "-1.2%" in tables[0].numbers


def test_unfetched_sources_become_down_weighted_search_excerpts():
    store = build_evidence_store(_rows(), manifest=_manifest())
    blog = [p for p in store.passages if p.source_id == "S2"]
    assert blog and all(p.kind == "search_excerpt" for p in blog)
    assert blog[0].reliability < 0.3 and blog[0].authority_tier == "low_quality"
    assert blog[0].published == ""


def test_search_ranks_by_relevance_and_reliability_with_per_source_cap():
    store = build_evidence_store(_rows(), manifest=_manifest())
    hits = store.search("financial services banks rotation", k=5, max_per_source=2)
    assert hits and hits[0][0].source_id == "S1"  # official page beats the blog on the same terms
    assert sum(1 for p, _ in hits if p.source_id == "S1") <= 2
    assert store.search("financial services", k=5, min_reliability=0.5) and all(
        p.reliability >= 0.5 for p, _ in store.search("financial services", k=5, min_reliability=0.5))


def test_positional_ids_without_manifest_and_summaries():
    store = build_evidence_store(_rows())
    assert store.source_ids() == ["S1", "S2", "S3"]
    summary = store.capture_summary()
    assert summary["passages"] == len(store.passages) and summary["sources"] == 3
    per_source = {row["sourceId"]: row for row in store.source_summary()}
    assert per_source["S1"]["authorityTier"] == "primary_official" and per_source["S1"]["kinds"]["table"] >= 2


def test_calculated_matrix_without_table_label_becomes_calculated_table_passages():
    matrix = "\n".join([
        "COMPLETED CALENDAR-QUARTER NSE SECTOR INDEX RETURNS (calculated from observed month-end index closes)",
        "Quarter coverage: Q3 FY26, Q4 FY26, Q1 FY27, Q2 FY27",
        "Sector | Q3 FY26 | Q4 FY26 | Q1 FY27 | Q2 FY27",
        "NIFTY IT | -4.10% | +2.20% | -1.30% | +6.50%",
        "NIFTY BANK | +3.40% | +1.10% | +5.20% | -0.80%",
        "NIFTY PHARMA | +1.00% | +0.40% | +2.70% | +3.30%",
    ])
    rows = [{"url": "internal://verified-sector-quarterly-returns", "title": "CALCULATED NSE SECTOR RETURNS — 4 COMPLETED CALENDAR QUARTERS",
             "fullContent": matrix, "pageFetchStatus": "not_applicable", "authorityTier": "primary_official", "reliabilityScore": 1.0}]
    store = build_evidence_store(rows)
    tables = [p for p in store.passages if p.kind == "calculated" and "Sector | Q3 FY26" in p.text]
    assert len(tables) == 1
    assert "NIFTY BANK | +3.40%" in tables[0].text and "Quarter coverage" in tables[0].text.splitlines()[0]
