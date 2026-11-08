"""Market-regime summary computed from the fetched sector dataset (Stage I, FIX 4).

The thesis of a sector-rotation report must come from the data, not from the writer's priors. This
module turns the completed-quarter sector matrix into fixed facts:

  * per-quarter equal-weight average return of each sector GROUP and of the benchmark;
  * the leading group in each quarter (``lead`` when the gap is at least ``MIN_GAP_PP``, else "mixed");
  * compounded group returns over the window (equal-weight average of each member's compounded return);
  * leaders and laggards (top/bottom 3 by compounded return) and the benchmark;
  * the leadership of the first and second half of the window, and the QTD group averages (labelled).

Groups are configurable through ``REGIME_GROUPS_JSON`` (a JSON object {group: [index labels]}); the
default classification is the conventional one for Indian sector indices:

  cyclical  - Metal, Auto, Realty, PSU Bank, Bank, Private Bank, Financial Services, Energy, Media,
              Consumer Durables
  defensive - FMCG, Pharma, Healthcare
IT is export-driven and belongs to neither group (it is listed separately, never called defensive).

Nothing here hardcodes a conclusion: the facts are recomputed for every run, and rotation claims in the
outline thesis and the report are checked against them.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger("regime")

MIN_GAP_PP = 1.0
DEFAULT_GROUPS = {
    "cyclical": ["NIFTY METAL", "NIFTY AUTO", "NIFTY REALTY", "NIFTY PSU BANK", "NIFTY BANK", "NIFTY PRIVATE BANK",
                 "NIFTY FINANCIAL SERVICES", "NIFTY ENERGY", "NIFTY MEDIA", "NIFTY CONSUMER DURABLES"],
    "defensive": ["NIFTY FMCG", "NIFTY PHARMA", "NIFTY HEALTHCARE"],
}
BENCHMARK = "NIFTY 50"
# What each sector is (used to stop mislabels such as "high-beta FMCG" or "defensive autos").
SECTOR_NATURE = {
    "FMCG": "defensive", "Pharma": "defensive", "Healthcare": "defensive",
    "Auto": "cyclical", "Autos": "cyclical", "autos": "cyclical", "Metal": "cyclical", "Metals": "cyclical", "Realty": "cyclical",
    "PSU Bank": "cyclical", "PSU Banks": "cyclical",  # Energy mixes oil and utilities: never relabelled
    "IT": "export-driven",
}


def groups() -> dict[str, list[str]]:
    raw = os.getenv("REGIME_GROUPS_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and all(isinstance(v, list) for v in data.values()):
                return {str(k): [str(x).upper() for x in v] for k, v in data.items()}
        except ValueError:
            log.warning("REGIME_GROUPS_JSON is not valid JSON; using the default groups")
    return DEFAULT_GROUPS


def _pretty(label: str) -> str:
    from utils.dataset_charts import pretty
    return pretty(label)


def _compound(values: list[float]) -> float:
    total = 1.0
    for v in values:
        total *= 1 + v / 100
    return round((total - 1) * 100, 2)


@dataclass
class Regime:
    quarters: list[str]
    group_returns: dict[str, list[float | None]]           # group -> per-quarter average
    benchmark: list[float | None]
    lead: list[str]                                         # per quarter: group name or "mixed"
    compounded: dict[str, float]                            # group -> compounded (avg of members)
    benchmark_compounded: float | None
    leaders: list[tuple[str, float]]
    laggards: list[tuple[str, float]]
    first_half: str
    second_half: str
    qtd: dict[str, float] = field(default_factory=dict)
    qtd_as_of: str = ""
    members: dict[str, list[str]] = field(default_factory=dict)

    @property
    def rotation(self) -> tuple[str, str] | None:
        """(from, to) when the leading group changed between the halves of the window."""
        if self.first_half != self.second_half and "mixed" not in (self.first_half, self.second_half):
            return self.first_half, self.second_half
        return None

    def facts_block(self) -> str:
        def pct(v):
            return "n/a" if v is None else f"{v:+.2f}%"
        lines = ["MARKET REGIME (computed in code from the fetched sector dataset; fixed facts - the thesis and every "
                 "rotation claim must agree with them):"]
        for name, members in self.members.items():
            lines.append(f"- {name.capitalize()} group (equal-weight): {', '.join(_pretty(m) for m in members)}.")
        lines.append("- IT is export-driven and is in neither group.")
        for i, q in enumerate(self.quarters):
            parts = [f"{g} {pct(vals[i])}" for g, vals in self.group_returns.items()]
            lines.append(f"- {q}: {', '.join(parts)}, {_pretty(BENCHMARK)} {pct(self.benchmark[i])}; leader: {self.lead[i]}.")
        comp = ", ".join(f"{g} {v:+.2f}%" for g, v in self.compounded.items())
        lines.append(f"- Compounded {self.quarters[0]} to {self.quarters[-1]}: {comp}; "
                     f"{_pretty(BENCHMARK)} {pct(self.benchmark_compounded)}.")
        lines.append(f"- Leaders (compounded): {', '.join(f'{_pretty(k)} {v:+.2f}%' for k, v in self.leaders)}.")
        lines.append(f"- Laggards (compounded): {', '.join(f'{_pretty(k)} {v:+.2f}%' for k, v in self.laggards)}.")
        lines.append(f"- Leadership by half of the window: first half {self.first_half}, second half {self.second_half}.")
        if self.qtd:
            lines.append(f"- Quarter to date ({self.qtd_as_of}, partial): "
                         + ", ".join(f"{g} {v:+.2f}%" for g, v in self.qtd.items()) + ".")
        return "\n".join(lines)

    def thesis_sentence(self) -> str:
        best = self.leaders[0] if self.leaders else None
        worst = self.laggards[0] if self.laggards else None
        comp = " and ".join(f"{g} sectors {v:+.2f}%" for g, v in self.compounded.items())
        rot = self.rotation
        trend = (f"leadership moved from {rot[0]} to {rot[1]} sectors" if rot else
                 f"leadership was {self.second_half if self.second_half != 'mixed' else 'mixed'} in the second half")
        lead = f"; {_pretty(best[0])} led ({best[1]:+.2f}%) and {_pretty(worst[0])} lagged ({worst[1]:+.2f}%)" if best and worst else ""
        bench = f", against {_pretty(BENCHMARK)} at {self.benchmark_compounded:+.2f}%" if self.benchmark_compounded is not None else ""
        return (f"From {self.quarters[0]} to {self.quarters[-1]}, {trend}: compounded, {comp}{bench}{lead}.")


def compute_regime(data) -> Regime | None:
    """``data`` is a dataset_charts.QuarterlyDataset."""
    if data is None or not data.rows:
        return None
    n = len(data.quarters)
    config = groups()
    members = {g: [m for m in labels if m in data.rows] for g, labels in config.items()}
    members = {g: m for g, m in members.items() if m}
    if len(members) < 2:
        return None
    group_returns: dict[str, list[float | None]] = {}
    for g, labels in members.items():
        per_q = []
        for i in range(n):
            vals = [data.rows[m].values[i] for m in labels if i < len(data.rows[m].values) and data.rows[m].values[i] is not None]
            per_q.append(round(sum(vals) / len(vals), 2) if vals else None)
        group_returns[g] = per_q
    bench_row = data.rows.get(BENCHMARK)
    benchmark = list(bench_row.values) if bench_row else [None] * n

    def leader(vals_by_group: dict[str, float | None]) -> str:
        known = {g: v for g, v in vals_by_group.items() if v is not None}
        if len(known) < 2:
            return "mixed"
        ranked = sorted(known.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[0][0] if ranked[0][1] - ranked[1][1] >= MIN_GAP_PP else "mixed"

    lead = [leader({g: group_returns[g][i] for g in group_returns}) for i in range(n)]
    compounded_by_sector: dict[str, float] = {}
    for label, row in data.rows.items():
        if label in data.compounded:
            compounded_by_sector[label] = data.compounded[label][0]
        elif row.values and all(v is not None for v in row.values):
            compounded_by_sector[label] = _compound(row.values)
    compounded = {}
    for g, labels in members.items():
        vals = [compounded_by_sector[m] for m in labels if m in compounded_by_sector]
        if vals:
            compounded[g] = round(sum(vals) / len(vals), 2)
    sectors = sorted(((k, v) for k, v in compounded_by_sector.items() if k != BENCHMARK), key=lambda kv: kv[1], reverse=True)
    half = n // 2

    def half_lead(lo: int, hi: int) -> str:
        avg = {}
        for g, vals in group_returns.items():
            known = [v for v in vals[lo:hi] if v is not None]
            avg[g] = _compound(known) if known else None
        return leader(avg)

    qtd = {}
    for g, labels in members.items():
        vals = [data.qtd[m][0] for m in labels if m in data.qtd]
        if vals:
            qtd[g] = round(sum(vals) / len(vals), 2)
    return Regime(quarters=list(data.quarters), group_returns=group_returns, benchmark=benchmark, lead=lead,
                  compounded=compounded, benchmark_compounded=compounded_by_sector.get(BENCHMARK),
                  leaders=sectors[:3], laggards=sectors[::-1][:3], first_half=half_lead(0, half),
                  second_half=half_lead(half, n), qtd=qtd, qtd_as_of=data.qtd_as_of, members=members)


# ── checks on prose ──────────────────────────────────────────────────────────────────────────────
_GROUP_WORD = r"(cyclicals?|defensives?|risk[- ]on|risk[- ]off|high[- ]beta|growth|value)"
_ROTATION_RE = re.compile(rf"(?i)\b(?:rotation|rotat\w+|shift\w*|transition\w*|mov\w+|migrat\w+)\b[^.]{{0,60}}?\bfrom\s+(?:the\s+)?"
                          rf"{_GROUP_WORD}\b[^.]{{0,40}}?\b(?:to|into|toward|towards)\s+(?:the\s+)?{_GROUP_WORD}")
_TOWARD_RE = re.compile(rf"(?i)\b(?:rotation|rotat\w+|shift\w*|transition\w*)\b[^.]{{0,25}}?\b(?:to|into|toward|towards)\s+(?:the\s+)?"
                        rf"(?:a\s+)?{_GROUP_WORD}")
_CANON = {"cyclical": "cyclical", "cyclicals": "cyclical", "risk-on": "cyclical", "risk on": "cyclical",
          "high-beta": "cyclical", "high beta": "cyclical", "defensive": "defensive", "defensives": "defensive",
          "risk-off": "defensive", "risk off": "defensive"}


def _canon(word: str) -> str:
    return _CANON.get(word.casefold(), "")


def contradicted_rotation(sentence: str, regime: Regime) -> str:
    """A reason when the sentence claims a rotation the computed regime does not show, else ''."""
    m = _ROTATION_RE.search(sentence)
    if m:
        src, dst = _canon(m.group(1)), _canon(m.group(2))
        if src and dst and src != dst:
            if regime.rotation != (src, dst):
                return (f"claims a rotation from {src} to {dst}; the data show first half {regime.first_half}, "
                        f"second half {regime.second_half}")
            return ""
    m = _TOWARD_RE.search(sentence)
    if m:
        dst = _canon(m.group(1))
        if dst and regime.second_half not in (dst, "mixed") and regime.compounded.get(dst, 0) <= max(regime.compounded.values()):
            other = regime.second_half
            if regime.compounded.get(other, 0) > regime.compounded.get(dst, 0):
                return (f"claims a rotation to {dst}; {other} sectors led the second half and compounded "
                        f"{regime.compounded.get(other, 0):+.2f}% vs {regime.compounded.get(dst, 0):+.2f}%")
    return ""


_SENT_RE = re.compile(r"[^.!?\n]*(?:[.!?](?=\s|$)|$)")


def check_rotation_claims(text: str, regime: Regime) -> list[dict]:
    out = []
    for m in _SENT_RE.finditer(str(text or "")):
        sentence = m.group(0).strip()
        if len(sentence) < 20:
            continue
        reason = contradicted_rotation(sentence, regime)
        if reason:
            out.append({"sentence": sentence[:300], "reason": reason, "start": m.start(), "end": m.end()})
    return out


def heal_rotation_claims(text: str, regime: Regime) -> tuple[str, list[dict]]:
    """The first contradicted sentence becomes the computed regime statement; later ones are removed."""
    claims = check_rotation_claims(text, regime)
    if not claims:
        return text, []
    actions = []
    replaced = False
    for claim in reversed(claims):  # back to front keeps offsets valid
        first = claim is claims[0]
        new = regime.thesis_sentence() if first and not replaced else ""
        lead = text[:claim["start"]]
        text = lead + (" " if new and lead and not lead.endswith((" ", "\n")) else "") + new + text[claim["end"]:]
        actions.append({"action": "rewritten from the computed regime" if new else "removed", **{k: claim[k] for k in ("sentence", "reason")}})
        if first:
            replaced = True
    log.warning("Regime check: %d rotation claim(s) contradicted the computed regime", len(claims))
    return re.sub(r"[ \t]{2,}", " ", text), actions[::-1]


_LABEL = r"(?i:high[- ]beta|cyclical|defensive)"
_SECTOR_NAMES = "|".join(sorted((re.escape(k) for k in SECTOR_NATURE), key=len, reverse=True))
_SECTOR_RE = re.compile(rf"\b({_SECTOR_NAMES})\b")  # case-sensitive: "IT" is not "it"
_LIST_RE = re.compile(rf"\b({_LABEL})(\s+(?:(?!and\b|or\b)[a-z-]+\s+)?(?i:sectors?|plays?|names|segments|stocks|"
                      rf"pockets|groups?)\s*(?:(?i:like|such as|including|led by)\s+|\(\s*))([^.;:)\n]{{1,90}})")
_BEFORE_RE = re.compile(rf"\b({_LABEL})\s+(?:(?!and\b|or\b|to\b|from\b)[a-z-]+\s+)?(?:Nifty\s+)?({_SECTOR_NAMES})\b")
_AFTER_RE = re.compile(rf"\b(?:Nifty\s+)?({_SECTOR_NAMES})\b(,?\s+(?:is\s+|as\s+|remains\s+|was\s+)?(?:a|an|the)\s+)({_LABEL})\b")


def _nature(sector: str) -> str:
    return SECTOR_NATURE.get(sector) or SECTOR_NATURE.get(sector.rstrip("s")) or ""


def _wrong(nature: str, label: str) -> bool:
    label = label.casefold().replace(" ", "-")
    return ((nature == "defensive" and label in {"high-beta", "cyclical"})
            or (nature == "cyclical" and label == "defensive")
            or (nature == "export-driven" and label in {"defensive", "cyclical"}))


def fix_mislabels(text: str) -> tuple[str, list[str]]:
    """'high-beta FMCG', 'defensive autos', 'IT, a defensive sector', 'high-beta sectors like Realty and FMCG'.

    A single sector with the wrong group word gets its own group word; a list that mixes natures loses
    the group word ("sectors like Realty and FMCG"); a list that shares one nature gets that one.
    """
    actions: list[str] = []

    def fix_list(m):
        label, members = m.group(1), m.group(3)
        natures = {_nature(x) for x in _SECTOR_RE.findall(members)} - {""}
        if not natures or not any(_wrong(n, label) for n in natures):
            return m.group(0)
        if len(natures) == 1:
            new = natures.pop()
            actions.append(f"'{label} ... {members.strip()[:40]}' relabelled {new}")
            return new + m.group(0)[len(label):]
        actions.append(f"'{label}' dropped before a mixed list ({members.strip()[:40]})")
        return m.group(0)[len(label):].lstrip()

    def fix_before(m):
        new = _nature(m.group(2))
        if not new or not _wrong(new, m.group(1)):
            return m.group(0)
        actions.append(f"'{m.group(0)}' relabelled {new}")
        return new + m.group(0)[len(m.group(1)):]

    def fix_after(m):
        new = _nature(m.group(1))
        if not new or not _wrong(new, m.group(3)):
            return m.group(0)
        actions.append(f"'{m.group(1)} ... {m.group(3)}' relabelled {new}")
        return m.group(0)[: m.start(3) - m.start(0)] + new

    text = _LIST_RE.sub(fix_list, str(text or ""))
    text = _BEFORE_RE.sub(fix_before, text)
    text = _AFTER_RE.sub(fix_after, text)
    text = re.sub(r"\b([Aa]) (export-driven)", lambda m: ("An " if m.group(1) == "A" else "an ") + m.group(2), text)
    # a sentence that now starts with the lower-case remainder of a dropped label keeps its capital
    text = re.sub(r"(^|[.!?]\s+|\n)(sectors|plays|names|segments|stocks)\b", lambda m: m.group(1) + m.group(2).capitalize(), text)
    return text, actions
