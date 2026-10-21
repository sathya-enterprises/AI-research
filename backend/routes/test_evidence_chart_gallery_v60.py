"""v60: evidence dataset extraction, full-catalog chart gallery and its audits."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[1].as_posix())

import routes.report as report
from utils.chart_catalog import normalize_chart_type
from utils.chart_evidence_audit import audit_chart_evidence_consistency
from utils.chart_gallery import GALLERY_ORDER, build_chart_gallery, gallery_markdown, gallery_types_for, requested_chart_types
from utils.evidence_dataset import build_evidence_dataset, extract_requested_entities
from utils.markdown_normalizer import normalize_report_markdown

QUESTION = (
    "Compare SBI, HDFC Bank, ICICI Bank, and Axis Bank using the latest comparable annual data available. "
    "Collect net profit, total assets, deposit growth, advances growth, and gross NPA ratios from annual reports."
)
BSE_TABLE = """As of 23rd August, the data given by BSE shows the following banks:

| (In Crores) | | | | | |
| --- | --- | --- | --- | --- | --- |
| Banks | M.C. | Net profit Q1 FY25 | Net profit Q1 FY24 | Total Assets | Gross NPA |
| HDFC Bank | 1238606.19 | 16174.75 | 11951.77 | 3567249.53 | 33025.69 |
| ICICI Bank | 847640.11 | 11059.11 | 9648.20 | 1892697.09 | 28,718.63 |
| SBI | 727578.99 | 17,035 | 16,884 | 1892697.09 | 91,328 |
| Kotak Mahindra Bank | 361330.87 | 6,249.82 | 3,452.30 | 604,947.82 | 6,251.42 |
| Axis Bank | 360459.34 | 6,034.64 | 5,797.10 | 14,68,163 | 1.54% (GNPA level) |
"""
VALUATION_TABLE = """Company | CMP (Rs) | Market Cap (Rs Cr) | PE Ratio | ROE | Dividend Yield
State Bank of India | 1,046 | 9,72,722 | 10.85 | 14.29% | 1.65%
HDFC Bank Ltd | 719 | 11,20,543 | 13.58 | 13.14% | 2.13%
ICICI Bank Ltd | 1,442 | 10,26,753 | 17.18 | 15.00% | 0.84%
Axis Bank Ltd | 1,260 | 3,90,358 | 13.97 | 12.53% | 0.08%
"""
NEWS = """Mumbai Oct 18, 2026 07:26 AM IST
HDFC Bank, the country's largest private sector lender, on Saturday reported a 4.7% rise in standalone net profit at Rs 19,510 crore in the July-September quarter as against Rs 18,641 crore a year ago.
Asset quality improved, with the gross NPA ratio declining to 1.11% at the end of September from 1.24% a year earlier and 1.17% in the preceding quarter.
"""
SOURCES = [
    {"id": "S2", "text": BSE_TABLE, "origin": "search_extract", "title": "BSE m-cap list"},
    {"id": "S8", "text": VALUATION_TABLE, "origin": "publisher_page", "title": "Banking stocks"},
    {"id": "S4", "text": NEWS, "origin": "search_extract", "title": "Q2 results"},
]


def _dataset():
    return build_evidence_dataset(SOURCES, QUESTION)


def test_requested_entities_and_aliases():
    assert extract_requested_entities(QUESTION) == ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank"]
    ds = _dataset()
    # "State Bank of India" / "HDFC Bank Ltd" resolve to the requested names.
    names = {o.entity for o in ds.observations if o.is_target}
    assert names == {"SBI", "HDFC Bank", "ICICI Bank", "Axis Bank"}


def test_table_and_prose_observations_with_periods_units_and_indian_grouping():
    ds = _dataset()
    best = ds.best()
    assert best[("hdfc bank", "net_profit", "Q1 FY25")].value == 16174.75
    assert best[("axis bank", "total_assets", "")].value == 1468163  # 14,68,163
    assert best[("axis bank", "gross_npa_ratio", "")].unit == "%"
    assert best[("sbi", "market_cap", "")].value == 972722  # publisher table preferred
    assert best[("hdfc bank", "net_profit", "Q2 FY27")].value == 19510
    assert best[("hdfc bank", "net_profit", "Q2 FY26")].value == 18641
    assert best[("hdfc bank", "net_profit_growth", "Q2 FY27")].value == 4.7
    assert best[("hdfc bank", "gross_npa_ratio", "Q1 FY27")].value == 1.17
    # SBI's total assets duplicate ICICI's row in the source → flagged, not charted.
    suspect = [o for o in ds.observations if o.suspect]
    assert [(o.entity, o.metric) for o in suspect] == [("SBI", "total_assets")]
    assert ("sbi", "total_assets", "") not in best


def test_all_requested_chart_types_parse_including_gynn():
    listed = ("Pie Chart Bar Chart Line Chart Area Chart Scatter Plot Bubble Chart Doughnut Chart Histogram Box Plot "
              "Waterfall Chart Funnel Chart Radar Chart Polar Area Chart Treemap Heatmap Stacked Bar Chart Grouped Bar Chart "
              "Horizontal Bar Chart Stacked Area Chart Step Line Chart Spline Chart Combo Chart Candlestick Chart OHLC Chart "
              "Gauge Chart Bullet Chart Pictogram Chart Pareto Chart Dot Plot Lollipop Chart Violin Plot Density Plot "
              "Ridgeline Plot Gantt Chart Timeline Chart Marimekko Chart Sunburst Chart Sankey Diagram Chord Diagram "
              "Network Graph Tree Diagram Flowchart Venn Diagram Funnel Pyramid Population Pyramid Progress Chart KPI Chart "
              "Sparkline Error Bar Chart Control Chart gynn chart")
    assert set(requested_chart_types(listed)) == set(GALLERY_ORDER)
    assert normalize_chart_type("gynn chart") == "gantt"
    assert gallery_types_for("Compare A and B") == GALLERY_ORDER  # default: full catalog


def test_gallery_charts_pass_quote_based_audit_after_json_round_trip():
    ds = _dataset()
    result = build_chart_gallery(ds, QUESTION, types=GALLERY_ORDER)
    included = set(result.included_types)
    assert {"grouped_bar", "bar", "pie", "scatter", "bubble", "heatmap", "radar", "boxplot", "waterfall",
            "gantt", "sankey", "venn", "kpi", "error_bar"} <= included
    # Unsupported types are listed with a reason, never faked.
    coverage = {row["type"]: row for row in result.coverage}
    assert coverage["candlestick"]["status"] == "Not included"
    assert "open/high/low/close" in coverage["candlestick"]["note"]
    body, charts = gallery_markdown(result, 1)
    assert "[CHART_1]" in body and "Chart coverage" in body
    sources = [{"id": sid, "url": f"https://example.com/{sid}", "chartEvidenceExcerpt": "\n".join(lines)}
               for sid, lines in result.evidence_lines.items()]
    payload = json.loads(json.dumps({"charts": charts, "sources": sources}))
    audit = audit_chart_evidence_consistency(body, payload["charts"], payload["sources"])
    assert audit["chartEvidenceFailures"] == []
    # A tampered value is caught.
    bad = json.loads(json.dumps(charts))
    for chart in bad:
        if chart["type"] == "bar":
            chart["series"][0]["data"][0]["value"] += 1
            break
    assert audit_chart_evidence_consistency(body, bad, payload["sources"])["chartEvidenceFailures"]


def test_entity_metric_search_plan_and_candidates_survive_topic_rewrite():
    class Intent:
        resolved_topic = "Indian private sector banks"
    assert report._extract_company_candidates(QUESTION, Intent()) == ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank"]
    queries = report._entity_metric_search_queries(QUESTION, "finance", today=date(2026, 10, 18))
    assert queries[0].startswith("SBI HDFC Bank ICICI Bank Axis Bank net profit total assets")
    assert "FY2025-26" in queries[0]
    assert any(q.startswith("SBI annual report FY26") for q in queries)
    assert not any("latest" in q.casefold() or "recent" in q.casefold() for q in queries)


def test_empty_code_fences_from_stripped_chart_json_are_removed():
    text = "Intro\n\n```\n```\n\nBody\n\n```json\n[CHART_2]\n```\n"
    assert normalize_report_markdown(text) == "Intro\n\nBody\n\n[CHART_2]"


def test_gallery_inserts_into_existing_data_and_charts_section():
    report_text = "# Summary\n\nText.\n\n# Data and Charts\n\nWriter table.\n\n# Risks\n\nRisk text.\n"
    out = report._insert_gallery_section(report_text, lambda level, heading: f"{'#' * level} Comparison\n\n[CHART_1]")
    assert out.index("Writer table.") < out.index("## Comparison") < out.index("# Risks")


def test_inline_layout_blends_charts_into_sections_without_a_chart_section():
    from utils.chart_gallery import blend_gallery_into_report
    ds = _dataset()
    result = build_chart_gallery(ds, QUESTION, types=["grouped_bar", "pie", "kpi", "gantt"])
    report_text = ("# Executive Summary\n\nOverview of the four banks.\n\n"
                   "# Profitability\n\nNet profit rose for all banks.\n\n"
                   "# Methodology\n\nSources and approach.\n")
    out, charts = blend_gallery_into_report(report_text, result, 1)
    assert "Data and Charts" not in out and len(charts) == 4
    sections = {h: out.index(h) for h in ("# Executive Summary", "# Profitability", "# Methodology")}
    def section_of(token):
        pos = out.index(token)
        return max((p, h) for h, p in sections.items() if p < pos)[1]
    placed = {charts[i]["type"]: section_of(f"[CHART_{i + 1}]") for i in range(len(charts))}
    assert placed["kpi"] == "# Executive Summary"
    assert placed["gantt"] == "# Methodology"
    assert placed["grouped_bar"] == "# Profitability"
