"""V2: a table chart set aside as a duplicate of a fetched-dataset chart comes back when that
dataset chart is dropped by verification.

Live run (internet use, India / Kenya / Brazil, 2015-2024): the World Bank table charts were dropped
as "same values as a fetched-dataset chart", then the official dataset charts themselves failed the
strict pre-writing verification, and the report shipped with no charts at all.
"""
import asyncio
import json
import re

from utils.evidence_store import build_evidence_store
from utils.llm_gateway import GatewayStats, LLMResult
from utils.report_pipeline_v2 import run_pipeline_v2

QUESTION = ("Compare internet use (% of individuals using the internet) and mobile-cellular subscriptions per 100 "
            "people in India, Kenya, and Brazil from 2015 to 2024.")
YEARS = list(range(2015, 2025))
INTERNET = {"Brazil": [58.33, 60.87, 67.47, 70.43, 73.91, 81.34, 80.69, 80.53, 84.15, 84.46],
            "India": [14.90, 16.50, 18.20, 20.08, 29.52, 43.41, 49.26, 55.90, 60.25, 64.94],
            "Kenya": [16.59, 16.60, 17.83, 19.50, 22.67, 24.72, 26.96, 29.41, 32.07, 34.98]}
MOBILE = {"Brazil": [127.8, 120.1, 112.9, 105.7, 98.8, 96.8, 102.5, 101.9, 103.6, 105.2],
          "India": [75.38, 84.79, 87.37, 86.94, 82.69, 82.04, 81.97, 81.52, 82.35, 84.36],
          "Kenya": [80.10, 79.28, 86.20, 96.33, 104.31, 114.21, 121.55, 123.67, 128.04, 130.80]}


def _official_row(code, measure, unit, data):
    lines = [f"OFFICIAL DATASET — World Bank, World Development Indicators: {measure} [{code}]",
             f"TABLE 1 — {measure}, 2015–2024", "Year | Brazil | India | Kenya"]
    for i, year in enumerate(YEARS):
        lines.append(f"{year} | {data['Brazil'][i]:.2f} | {data['India'][i]:.2f} | {data['Kenya'][i]:.2f}")
    text = "\n".join(lines)
    return {"url": f"https://data.worldbank.org/indicator/{code}", "title": f"World Bank: {measure}, 2015-2024",
            "publisher": "World Bank", "pageFetchStatus": "ok", "fullContent": text, "pageContent": text,
            "kind": "Official dataset", "sourceType": "Official dataset", "evidenceOrigin": "publisher_page",
            "authorityTier": "primary_official", "reliabilityScore": 1.0,
            "officialDataset": {"id": code, "code": code, "unit": unit, "entities": ["Brazil", "India", "Kenya"],
                                "years": YEARS, "fit": "direct", "measure": measure}}


def _store():
    rows = [_official_row("IT.NET.USER.ZS", "Individuals using the Internet (% of population)", "% of population", INTERNET),
            _official_row("IT.CEL.SETS.P2", "Mobile cellular subscriptions (per 100 people)", "per 100 people", MOBILE)]
    narrative = ("Affordable smartphones and cheap mobile data drove internet adoption in India, Kenya and Brazil, "
                 "while national broadband plans extended rural coverage and mobile money raised demand in Kenya. ") * 5
    for i in range(3, 13):
        rows.append({"url": f"https://www.itu.int/digital-access-{i}", "title": f"Digital access drivers report {i}",
                     "publisher": "ITU", "publication_date": "2025-06-01", "pageFetchStatus": "ok",
                     "pageContent": narrative + f" Rural coverage rose in survey wave {i}.",
                     "authorityTier": "institutional_research", "reliabilityScore": 0.85})
    manifest = [{"id": f"S{i}", "url": r["url"], "title": r["title"], "publisher": r["publisher"],
                 "authority_tier": r["authorityTier"], "reliability_score": r["reliabilityScore"],
                 "kind": r.get("kind", "Web source"), "source_type": r.get("sourceType", "Web source"),
                 "publication_date": r.get("publication_date", "")} for i, r in enumerate(rows, 1)]
    return build_evidence_store(rows, question=QUESTION, manifest=manifest), manifest


OUTLINE = {
    "title": "Digital Access in India, Kenya and Brazil, 2015-2024",
    "thesis": "Brazil leads on internet use while Kenya leads on mobile subscriptions; India grew fastest.",
    "entities": [{"name": "India", "aliases": []}, {"name": "Kenya", "aliases": []}, {"name": "Brazil", "aliases": []}],
    "dimensions": ["internet use", "mobile-cellular subscriptions"],
    "sections": [
        {"title": "Internet use trends", "goal": "Trends", "queries": ["Individuals using the Internet population Brazil India Kenya"]},
        {"title": "Mobile subscription trends", "goal": "Trends", "queries": ["Mobile cellular subscriptions per 100 people"]},
        {"title": "Latest country gaps", "goal": "Gaps", "queries": ["internet use 2024 Brazil India Kenya"]},
        {"title": "Drivers of digital access", "goal": "Drivers", "queries": ["smartphones mobile data broadband adoption"]},
        {"title": "Rural coverage and mobile money", "goal": "Drivers", "queries": ["rural coverage mobile money Kenya"]},
    ],
}


class FakeGateway:
    def __init__(self):
        self.stats = GatewayStats()

    async def generate(self, *, purpose, prompt, system="", section_id="", **kwargs):
        self.stats.calls += 1
        self.stats.by_purpose[purpose] += 1
        self.stats.models["fake"] += 1
        if purpose == "outline":
            return LLMResult(ok=True, text=json.dumps(OUTLINE), model="fake")
        if purpose in {"measure_plan", "official_data_plan", "dataset_extract"}:
            return LLMResult(ok=True, text="{}", model="fake")
        if purpose == "editorial_transitions":
            return LLMResult(ok=True, text="{}", model="fake")
        ids = re.findall(r"\[(S\d+\.\d+)\]", prompt) or ["S3.1"]
        if purpose == "executive_summary":
            return LLMResult(ok=True, text=f"Smartphones and cheap data drove adoption [{ids[0]}].", model="fake")
        figs = [int(n) for n in re.findall(r"(?m)^Figure (\d+):", prompt)]
        body = "### Evidence\n" + (
            "Affordable smartphones and cheap mobile data drove adoption across the three countries, while national "
            "broadband plans extended rural coverage and mobile money raised everyday demand for connectivity "
            f"[{ids[-1]}]. ") * 6
        for n in figs:
            body += f"\n\nFigure {n} shows how the three countries compare [{ids[0]}].\n\n[[FIGURE {n}]]"
        return LLMResult(ok=True, text=body + f"\n<<<SUMMARY>>>\nAdoption rose [{ids[0]}].", model="fake")


def _run(verifier):
    store, manifest = _store()
    result = asyncio.run(run_pipeline_v2(runtime=None, question=QUESTION, tier="detailed", store=store,
                                         manifest=manifest, today="2026-11-13", gateway=FakeGateway(),
                                         chart_verifier=verifier))
    return json.loads(result["raw"]), result["meta"]["charts"]


def _is_official_chart(spec: dict) -> bool:
    # Official dataset charts are titled with the bare measure; table charts add ": Brazil, India and Kenya, ..."
    return ":" not in str(spec.get("title") or "")


def test_table_charts_wait_behind_official_charts_that_pass():
    out, meta = _run(lambda specs, report_text="": [""] * len(specs))
    duplicate_drops = [d for d in meta["droppedBeforeWriting"] if "duplicate view" in d["reason"]]
    titles = [c["title"] for c in out["charts"]]
    assert len([t for t in titles if "Individuals using the Internet" in t]) == 1  # one chart, not two views
    assert duplicate_drops  # the table view is still treated as a duplicate while the official chart stands


def test_generic_value_axis_does_not_replace_the_chart_metric_in_titles():
    def verifier(specs, report_text=""):
        return ["outside window" if not report_text and _is_official_chart(spec) else "" for spec in specs]

    out, _meta = _run(verifier)
    assert all(not re.search(r"Figure \d+: Value\b", c["title"]) for c in out["charts"]), [c["title"] for c in out["charts"]]


def test_table_chart_is_restored_when_the_official_chart_it_deferred_to_is_dropped():
    def verifier(specs, report_text=""):
        return ["The chart source period falls outside the time window requested by the user."
                if not report_text and _is_official_chart(spec) else "" for spec in specs]

    out, meta = _run(verifier)
    titles = [c["title"] for c in out["charts"]]
    dropped_official = [d for d in meta["droppedBeforeWriting"] if "failed chart verification" in d["reason"]]

    assert dropped_official, "the verifier should have dropped the official charts"
    assert any("Individuals using the Internet" in t for t in titles), titles
    assert not [d for d in meta["droppedBeforeWriting"] if "duplicate view" in d["reason"]
                and "Individuals using the Internet" in str(d["title"])]
