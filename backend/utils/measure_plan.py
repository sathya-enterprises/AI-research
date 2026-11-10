"""Stage M: the research plan of MEASURES - what the report must quantify, for any topic.

The engine holds no domain list. One LLM call turns the question into the measures a report needs
(what is measured, for which entities, how it should be compared); code validates the shape; every
measure becomes data-seeking searches ("<measure> <entity> statistics by year 2016 2017 ..."), so the
year-by-year tables charts are made from are looked for on purpose, not found by luck.

    plan = await plan_measures(question, gateway, today=..., tier=...)
    queries = measure_queries(plan, question, today=..., tier=...)

The plan is also handed to the official-data planner (which datasets measure these things) and recorded
in the capture. When the call fails, a deterministic one-measure plan is built from the question words
(the Stage J data angle), so a report never depends on this call.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

log = logging.getLogger("measure_plan")

CHART_KINDS = {"trend", "comparison", "share", "ranking"}
MAX_MEASURES = {"detailed": 4, "in_depth": 6}
MAX_QUERIES = {"detailed": 4, "in_depth": 8}

SYSTEM = ("You plan the quantitative evidence for a research report on ANY topic. You name the measurable "
          "quantities that would answer the question, as an analyst would before searching. Return JSON only.")
PROMPT = """QUESTION: {question}
TODAY: {today}
TIME WINDOW: {window}

List the {n} most important MEASURES the report must show with numbers (charts and tables), most important first.
A measure is a quantity that some publisher reports over time or across entities - e.g. "share of new car sales
that are electric", "net foreign portfolio investment flows", "measles vaccination coverage of one-year-olds",
"installed solar capacity", "number of data-centre projects announced".

Return {{"measures": [{{"measure": "<the quantity, plain words, no numbers>",
  "entities": ["<country / company / sector / programme / group it is needed for>", ...],
  "chart": "trend" | "comparison" | "share" | "ranking",
  "why": "<one line: which part of the question it answers>"}}]}}
Rules: 2-{n} measures; 1-5 entities each, taken from the question or the obvious peers it implies; never invent
data values; prefer measures an official or primary publisher (statistics office, regulator, industry body,
international organisation, company filing) is likely to publish."""


@dataclass
class Measure:
    measure: str
    entities: list[str] = field(default_factory=list)
    chart: str = "trend"
    why: str = ""

    def to_dict(self) -> dict:
        return {"measure": self.measure, "entities": self.entities, "chart": self.chart, "why": self.why}


@dataclass
class MeasurePlan:
    measures: list[Measure]
    method: str = "llm"
    rejected: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"method": self.method, "measures": [m.to_dict() for m in self.measures], "rejected": self.rejected}

    def brief(self) -> str:
        """One line per measure, for other planners' prompts."""
        return "\n".join(f"- {m.measure}" + (f" ({', '.join(m.entities)})" if m.entities else "") for m in self.measures)


def _clean(text: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" .;:-")[:limit]


def validate(data: object, *, tier: str) -> MeasurePlan:
    """Keep only well-formed measures (shape checks; nothing about any domain)."""
    plan = MeasurePlan(measures=[])
    items = data.get("measures") if isinstance(data, dict) else None
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        measure = _clean(item.get("measure"), 90)
        if len(measure) < 6 or re.search(r"\d{2,}", measure.replace("2.5", "")) and not re.search(r"(?i)pm\s?2\.5|co2|5g|4g", measure):
            plan.rejected.append({"measure": measure, "reason": "empty, too short or carries numbers"})
            continue
        if measure.casefold() in seen:
            continue
        seen.add(measure.casefold())
        entities = [_clean(e, 50) for e in (item.get("entities") or []) if _clean(e, 50)][:5]
        chart = str(item.get("chart") or "trend").strip().casefold()
        plan.measures.append(Measure(measure=measure, entities=list(dict.fromkeys(entities)),
                                     chart=chart if chart in CHART_KINDS else "trend", why=_clean(item.get("why"), 160)))
    plan.measures = plan.measures[: MAX_MEASURES.get(tier, 4)]
    return plan


def fallback_plan(question: str) -> MeasurePlan:
    from utils.data_angles import subject_terms
    terms = subject_terms(question)
    return MeasurePlan(measures=[Measure(measure=" ".join(terms))] if len(terms) >= 2 else [], method="keywords (planner unavailable)")


async def plan_measures(question: str, gateway, *, today: date, tier: str) -> MeasurePlan:
    """Never raises; falls back to a keyword plan."""
    n = MAX_MEASURES.get(tier, 4)
    try:
        from utils.data_angles import window_years
        years = window_years(question, today)
        window = f"{years[0]}-{years[-1]}" if years else "the period the question asks about"
    except Exception:
        window = "the period the question asks about"
    if gateway is not None:
        try:
            result = await gateway.generate(purpose="measure_plan", section_id="plan", system=SYSTEM,
                                            prompt=PROMPT.format(question=str(question)[:2000], today=today.isoformat(),
                                                                 window=window, n=n),
                                            max_tokens=1200, temperature=0.0, json_mode=True, timeout=60, retries=1)
            data = result.json() if getattr(result, "ok", False) else None
            plan = validate(data, tier=tier)
            if len(plan.measures) >= 2:
                log.info("Measure plan: %s", "; ".join(m.measure for m in plan.measures))
                return plan
        except Exception as exc:  # the plan must never abort a report
            log.warning("Measure plan failed: %s", type(exc).__name__)
    return fallback_plan(question)


def measure_queries(plan: MeasurePlan, question: str, *, today: date, tier: str, existing: list[str] | None = None) -> list[str]:
    """Data-seeking searches: per measure, one statistics query (by entity when there are entities) and,
    for in-depth reports, one table / report query; capped per tier."""
    try:
        from utils.data_angles import window_years
        years = window_years(question, today)
    except Exception:
        years = []
    span = f"{years[0]} to {years[-1]}" if len(years) >= 2 else ""
    out: list[str] = []
    for m in plan.measures:  # every measure gets its statistics query before any gets a second one
        who = " ".join(m.entities[:2])
        out.append(f"{m.measure} {who} statistics by year {span} official data".strip())
    if tier == "in_depth":
        for m in plan.measures:
            who = " ".join(m.entities[:2])
            kind = "by country" if m.chart in {"comparison", "ranking"} and not m.entities else "annual"
            out.append(f"{m.measure} {who} {kind} data table report".strip())
    have = {q.casefold() for q in existing or []}
    deduped = list(dict.fromkeys(q for q in (re.sub(r"\s+", " ", x) for x in out) if q.casefold() not in have))
    return deduped[: MAX_QUERIES.get(tier, 4)]
