"""Stage G: every gate self-heals; every false alarm found so far is a permanent regression test,
and a genuinely wrong value is still removed."""
from utils.evidence_check import _reconcile_groups
from utils.evidence_store import store_from_passages
from utils.fact_sheet import FactSheet
from utils.gate_healing import gate_table, hard_findings, heal, place_qualifiers, soften_scope


def _store(passages: dict):
    return store_from_passages({pid: {"id": pid, "text": text} for pid, text in passages.items()})


def _cleared(report: str, claim: str, sources: list[str], values: list[str], passages: dict) -> bool:
    store = _store(passages)
    kept = _reconcile_groups([{"claim": claim, "sourceIds": sources, "unmatchedNumericClaims": values}],
                             report, FactSheet(store.passages), store, [])
    return not kept


# ── false alarms (correct claims report.py flagged) ─────────────────────────────────────────────
def test_minus_52_one_year_return():
    report = ("For example, Sterling and Wilson Renewable Energy Ltd. Saw promoter shareholding fall by -15.3 "
              "(calculated: 49.0 - 64.3) percentage points, while its stock delivered a 1-year return of -52% [S91].")
    src = {"S91.3": "Company Free Float Mkt.Cap (INR BN) 1Yr. Return\nSterling and Wilson Renewable Energy Ltd. 54% 72 -52%\n"
                    "Promoters 64.3 64.3 50.4 49.0 49.0"}
    assert _cleared(report, report[:200], ["S91"], ["percent:-52"], src)


def test_psu_bank_momentum_and_underperformance_from_a_table_row():
    report = ('By late 2026 the Nifty PSU Bank index was in the "Lagging" quadrant with a momentum score of 98.5, '
              "underperforming the Nifty 50 by 4.64% over four weeks [S13].")
    src = {"S13.2": "| Index | Quadrant | RS-Ratio | RS-Momentum | 4-week vs Nifty 50 | 13-week vs Nifty 50 |\n|---|---|---|---|---|---|\n"
                    "| Nifty PSU Bank | Lagging | 99.5 | 98.5 | -4.64% | -3.77% |\n| Nifty Bank | Weakening | 100.3 | 97.4 | -3.79% | +0.44% |"}
    assert _cleared(report, report, ["S13"], ["scalar:98.5", "percent:4.64"], src)


def test_lagged_by_7_10_matches_minus_7_10():
    report = "The Nifty FMCG index lagged the Nifty 50 by 7.10% over 13 weeks [S13]."
    src = {"S13.2": "| Index | Quadrant | 4-week vs Nifty 50 | 13-week vs Nifty 50 |\n|---|---|---|---|\n"
                    "| Nifty FMCG | Lagging | -6.17% | -7.10% |\n| Nifty Energy | Lagging | -7.31% | -6.61% |"}
    assert _cleared(report, report, ["S13"], ["percent:7.1"], src)


def test_inr52bn_is_52_billion():
    report = "In railways, RVNL secured ₹52 billion of orders, 17% of its guidance [S137]."
    src = {"S137.2": "In railways, RVNL secured INR52bn (17% of its INR300bn guidance), while IRCON lagged."}
    assert _cleared(report, report, ["S137"], ["inr_crore:5200~50"], src)


def test_fpi_flow_escalation_minus_14116_to_minus_47955():
    report = "Selling pressure escalated from -14,116 crore in mid-September to -47,955 crore by mid-October [S17]."
    src = {"S17.6": "| Fortnight ending | FPI net investment (crore) |\n|---|---|\n| 15 Sep 2026 | -14,116 |\n"
                    "| 30 Sep 2026 | -21,745 |\n| 15 Oct 2026 | -47,955 |"}
    assert _cleared(report, report, ["S17"], ["scalar:-14116", "scalar:-47955"], src)


def test_ongc_acronym_matches_the_full_company_name():
    report = "Energy names (ONGC, weighted at 2.36%) [S40] faced sharp pressure."
    src = {"S40.1": "Index weights\nPower Grid Corporation of India Ltd. 3.23\nOil & Natural Gas Corporation Ltd. 2.36"}
    assert _cleared(report, "(ONGC, weighted at 2.36%) [S40]", ["S40"], ["percent:2.36"], src)


def test_only_the_flagged_sentence_of_a_bullet_is_judged():
    report = ("* **Textiles:** This sector saw net outflows of ₹177 crore in the first half of March 2026, with AUC falling "
              "from ₹18,124 crore to ₹16,822 crore [S56]. By late September it saw a net investment of ₹636 crore.")
    src = {"S56.11": "| Sector | Net investment (crore) | AUC (crore) | AUC earlier (crore) |\n|---|---|---|---|\n"
                     "| Textiles | -177 | 16,822 | 18,124 |\n| Telecom | 120 | 3,000 | 2,900 |"}
    assert _cleared(report, report[:180], ["S56"], ["inr_crore:177", "inr_crore:18124", "inr_crore:16822"], src)


def test_numbers_citing_two_groups_in_one_sentence():
    report = ("Construction Materials saw a cumulative net reduction of -₹564 crore [S140], whereas Textiles outflows "
              "totaled -₹964 crore [S127].")
    src = {"S140.4": "| Sector | Cumulative FPI flow (crore) |\n|---|---|\n| Construction Materials | -564 |\n| Cement | -100 |",
           "S127.2": "| Sector | 1Y flow (crore) |\n|---|---|\n| Textiles | -964 |\n| Media | -50 |"}
    assert _cleared(report, report, ["S140"], ["inr_crore:-564", "inr_crore:-964"], src)


# ── a genuinely wrong value is still removed ─────────────────────────────────────────────────────
def test_hg_infra_45_billion_with_an_invented_calculation_is_removed():
    report = ("* **Road EPCs:** Ceigall secured ₹28 billion (calculated: 22.9 + 5) in inflows and HG Infra secured "
              "₹45 billion [S137, S14].")
    src = {"S137.2": "Road EPC order inflows: Ceigall INR22.9bn, GR Infra INR62bn.", "S14.1": "Auto 31.4 30.2 4 39.8"}
    assert not _cleared(report, report, ["S137", "S14"], ["inr_crore:4500~50"], src)


# ── self-healing behaviour ───────────────────────────────────────────────────────────────────────
def _rerun_factory(results):
    calls = iter(results)
    return lambda text, charts: next(calls)


def test_duplicate_table_is_dropped_and_gates_rerun():
    table = "**Table: QTD return**\n\n| Sector | QTD return to Nov 2026 (%) |\n| --- | --- |\n| NIFTY AUTO | +9.1% |\n| NIFTY IT | -1.2% |"
    text = f"## A\n\n{table}\n\nText.\n\n## B\n\n{table}\n\nMore text."
    audits = {"publicationAudit": {"duplicateTableCount": 1}, "sourceEvidenceAudit": {}, "scopeCoverageAudit": {}}
    clean = {"publicationAudit": {}, "sourceEvidenceAudit": {}, "scopeCoverageAudit": {}}
    healed, _, after, summary = heal(text, [], audits, _rerun_factory([clean, clean]))
    assert healed.count("| NIFTY AUTO |") == 1 and not summary["stillFailing"]
    assert any(a["gate"] == "duplicateTableCount" for a in summary["actions"])


def test_unsupported_claim_is_removed_and_listed():
    text = "## Flows\n\nFPIs sold ₹99,999 crore of IT stocks in October [S9]. DIIs bought steadily."
    group = {"claim": "FPIs sold ₹99,999 crore of IT stocks in October", "sourceIds": ["S9"],
             "unmatchedNumericClaims": ["inr_crore:99999"]}
    audits = {"publicationAudit": {}, "sourceEvidenceAudit": {"mismatchedCitationGroups": [group]}, "scopeCoverageAudit": {}}
    clean = {"publicationAudit": {}, "sourceEvidenceAudit": {}, "scopeCoverageAudit": {}}
    store = _store({"S9.1": "FPIs sold IT stocks worth ₹11,000 crore in October."})
    healed, _, _, summary = heal(text, [], audits, _rerun_factory([clean, clean]), store=store)
    assert "99,999" not in healed and "DIIs bought steadily" in healed
    assert summary["removed"] == 1 and not summary["stillFailing"]


def test_scope_findings_are_soft_qualifiers_never_blocking():
    audits = {"scopeCoverageAudit": {"blocking": True, "flags": [
        "An in-window FTA signature claim or no-signing result must cite a verified official Indian source."]}}
    actions = []
    qualifiers = soften_scope(audits, actions)
    assert audits["scopeCoverageAudit"]["blocking"] is False and qualifiers
    assert "Official confirmation not found" in qualifiers[0]
    text = place_qualifiers("## Analysis\n\nBody.\n\n## Methodology and Evidence Base\n\nHow.", qualifiers)
    assert text.index("[!NOTE:Verification notes]") < text.index("## Methodology")
    assert hard_findings(audits) == []


def test_gate_table_lists_hard_and_soft_gates_with_remedies():
    table = gate_table()
    assert any(g["key"] == "mismatchedCitationGroups" and g["blocking"] for g in table)
    assert any(g["key"] == "scopeFlags" and not g["blocking"] for g in table)
    assert all(g["remedy"] for g in table)


def test_dash_cells_are_empty_not_unverified():
    from utils.report_pipeline_v2 import blank_dash_cells
    md = "| Sector | 1Y | QTD |\n| --- | --- | --- |\n| Realty | — | -2.1% |\n| Media | - | 1.0% |"
    out = blank_dash_cells(md)
    assert "—" not in out and "| --- |" in out and "| Media | | 1.0% |" in out and "| Realty | | -2.1% |" in out


def test_sansad_and_indian_trade_portal_are_official_fta_sources():
    from utils.deep_research_coverage import _official_fta_signature_source
    for url in ("https://sansad.in/getFile/annex.pdf", "https://www.indiantradeportal.gov.in/x", "https://pib.gov.in/x"):
        row = {"id": "S1", "url": url, "pageFetchStatus": "ok",
               "_publisherRawFetchedContent": "FREE TRADE AGREEMENTS (FTAS) SIGNED BY INDIA: India-Oman CEPA signed 18th December 2025",
               "pageContent": "Annexure"}
        assert _official_fta_signature_source([row], {"S1"}), url


def test_agreements_before_the_window_become_compact_earlier_past_sections():
    from datetime import date
    from utils.agreement_ledger import partner_windows
    from utils.question_asks import parse_asks, repair_for_asks
    from utils.report_outline import Section
    store = _store({"S16.8": "5 India - UK Comprehensive Economic and Trade Agreement (CETA) 24th July 2025 15th July 2026 "
                             "6 India – Oman CEPA 18th December 2025 01st June 2026 2 India-UAE CEPA 18th February, 2022 1st May 2022"})
    windows = partner_windows(store, date(2025, 11, 8), date(2026, 11, 8))
    assert windows == {"uk": True, "oman": True, "uae": False}
    asks = parse_asks("List down the why, what, how of all the FTAs signed by India in the past 1 year.", date(2026, 11, 8))
    uae = Section("sec-1", "The India-UAE CEPA", role="deep_dive", entity="India-UAE CEPA")
    oman = Section("sec-2", "India-Oman CEPA", role="deep_dive", entity="India-Oman CEPA")
    n = [2]

    def factory(title, goal, queries):
        n[0] += 1
        return Section(f"sec-{n[0]}", title, goal=goal, queries=queries)

    repair_for_asks(asks, [uae, oman], [], factory, partner_windows=windows)
    assert uae.title.startswith("Comparison with the earlier past") and uae.role == "compact" and not uae.subsections
    assert [s.title for s in oman.subsections] == ["Why it was signed", "What it covers", "How it works and is implemented"]
