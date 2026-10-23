from datetime import date

from routes.house_styles import (
    house_print_css, is_monthly_market_review, normalize_report_style, render_house_cover, review_period,
    running_title, transform_section_html, writer_style_contract,
)
from routes.html_report import build_html_report

TODAY = date(2026, 10, 22)


def test_style_normalisation_and_default():
    assert normalize_report_style("Market Currents") == "market_currents"
    assert normalize_report_style("feature-article") == "feature_article"
    assert normalize_report_style("unknown") is None
    assert normalize_report_style(None, "market_currents") == "market_currents"


def test_monthly_review_detection_and_period():
    assert is_monthly_market_review("Monthly market review")
    assert is_monthly_market_review("June 2026 market report")
    assert is_monthly_market_review("Prepare India : Market Currents")
    # "Mar" inside "Market" and "may" the verb are not months.
    assert not is_monthly_market_review("How may markets react to the RBI policy report")
    assert not is_monthly_market_review("HDFC Bank Q2 results analysis")
    assert review_period("Monthly market review for June 2026", TODAY) == ("June", 2026)
    assert review_period("India : Market Currents", TODAY) == ("September", 2026)
    assert review_period("Dec market review", date(2026, 1, 5)) == ("December", 2025)
    assert running_title("market_currents", "", "June 2026 market report", TODAY) == "Market Currents – Jun’26"


def test_writer_contract_fixes_monthly_sections_and_describes_feature():
    monthly = writer_style_contract("market_currents", "Monthly market review for June 2026", "detailed", TODAY)
    for heading in ("## Executive Summary", "## Index Performance — The Month in One Chart", "## Institutional Flows — FII vs DII",
                    "## Outlook & Strategic Takeaways", "Key Watch-Points for July 2026"):
        assert heading in monthly
    assert "India : Market Currents — June 2026" in monthly
    brief = writer_style_contract("market_currents", "Monthly market review for June 2026", "brief", TODAY)
    assert "## Markets, Sectors & Flows" in brief and "## Sectoral Performance" not in brief
    topical = writer_style_contract("market_currents", "HDFC Bank outlook", "detailed", TODAY)
    assert "Desk View" in topical and "MONTHLY REVIEW STRUCTURE" not in topical
    feature = writer_style_contract("feature_article", "FPI selling and the rupee", "detailed", TODAY)
    assert "kicker" in feature and "standfirst" in feature
    assert writer_style_contract(None, "anything", "detailed", TODAY) == ""


def test_section_transforms_callouts_timelines_and_badges():
    html = (
        '<blockquote class="gg-reveal gg-pullquote" data-reveal><strong>Desk View:</strong> Markets steadied.</blockquote>'
        '<ul class="gg-reveal gg-list" data-reveal><li><strong>Jun 5</strong> — RBI holds.</li><li><strong>Jun 22 – 26</strong> — VIX falls.</li></ul>'
        '<ul class="gg-reveal gg-list" data-reveal><li><strong>Banks</strong> — led.</li><li>Plain item</li></ul>'
        '<td class="gg-cell-numeric">+6.41%</td><td class="">-9.56%</td><td class="">▲ Firm</td><td class="">23,865.75</td>'
    )
    out = transform_section_html("market_currents", html)
    assert '<aside class="gg-house-callout"><div class="gg-house-callout__kicker">Desk View</div><p>Markets steadied.</p></aside>' in out
    assert out.count('<ol class="gg-house-timeline">') == 1 and "Jun 22 – 26" in out
    assert "<li><strong>Banks</strong> — led.</li>" in out  # non-date list untouched
    assert 'gg-badge--pos">+6.41%' in out and 'gg-badge--neg">-9.56%' in out and 'gg-badge--pos">▲ Firm' in out
    assert '<td class="">23,865.75</td>' in out
    assert transform_section_html(None, html) == html


def test_covers():
    stats = [{"label": "NIFTY 50 (CLOSE)", "value": "23,865.75", "change": "+1.67% MoM"},
             {"label": "NIFTY IT", "value": "-9.56%", "change": "Worst sector"}]
    cover = render_house_cover("market_currents", title="India : Market Currents — June 2026", summary="", question="June 2026 market report",
                               date_str="22 October 2026", key_stats=stats, today=TODAY)
    assert "Monthly Market Intelligence" in cover and "June 2026" in cover and "India : Market Currents" in cover
    assert "gg-tone--pos" in cover and "gg-tone--neg" in cover and "data:image/jpeg;base64," in cover
    feature = render_house_cover("feature_article", title="A headline", summary="The standfirst.", question="q", date_str="22 October 2026",
                                 key_stats=[], report_text="word " * 660, kicker="A hard sell")
    assert "A hard sell" in feature and "3 min read" in feature and "The standfirst." in feature


def test_print_furniture_carries_running_title_and_arn():
    css = house_print_css("market_currents", "Market Currents – Jun’26")
    assert '"Market Currents – Jun’26"' in css and "ARN-351151" in css and '"Growth Gradual LLP"' in css
    assert "@@" not in css and "html:has(body[data-report-style=\"market_currents\"])" in css


def test_build_html_report_applies_style_and_keeps_default_unchanged():
    report = "## Executive Summary\n\nMarkets rose [S1].\n\n> **Desk View:** Steady [S1].\n"
    sources = [{"id": "S1", "title": "NSE", "url": "https://www.nseindia.com/x"}]
    stats = [{"label": "NIFTY 50 (CLOSE)", "value": "23,865.75", "change": "+1.67% MoM", "sourceIds": ["S1"]}]
    styled = build_html_report(report, "India : Market Currents — June 2026", "June 2026 market report", "", stats, [], [],
                               sources=sources, report_style="market_currents")
    assert 'data-report-style="market_currents"' in styled and "gg-house--market_currents" in styled
    assert "Section 01" in styled and "gg-house-callout" in styled and "gg-house-kpis" in styled and "Disclaimer:" in styled
    plain = build_html_report(report, "India : Market Currents — June 2026", "June 2026 market report", "", stats, [], [], sources=sources)
    assert "data-report-style" not in plain and "gg-house-callout" not in plain and "Disclaimer:" not in plain


def test_excluded_evidence_sections_never_render():
    report = ("## Evidence-Based Research Record\n\nProviders were unavailable.\n\n**Selected report depth:** brief\n\n"
              "## Policy Impact\n\n### Source-Derived Evidence\n\n- **S1 — RBI**: long excerpt [S1]\n\n"
              "### Evidence Gaps and Interpretation\n\nBoilerplate.\n\n### Rates\n\nRepo rate held at 5.25% [S1].\n\n"
              "## Source-Extracted Quantitative Evidence\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n## Research Provenance\n\nUsed 21 sources.\n")
    sources = [{"id": "S1", "title": "RBI", "url": "https://www.rbi.org.in/x"}]
    for style in (None, "market_currents", "feature_article"):
        html_doc = build_html_report(report, "RBI rate cut", "RBI rate cut impact", "", [], [], [], sources=sources, report_style=style)
        for banned in ("Source-Derived Evidence", "Evidence Gaps and Interpretation", "Evidence-Based Research Record",
                       "Source-Extracted", "Research Provenance", "Selected report depth", "long excerpt"):
            assert banned not in html_doc, (style, banned)
        assert "Repo rate held at 5.25%" in html_doc
