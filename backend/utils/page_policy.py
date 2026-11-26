"""Page policy (Stage D): length comes from evidence volume, with a depth floor per mode.

  * floor  -- the minimum depth a mode aims for. Below it, more pages are written
              only from evidence the report has not used yet (V2: evidence-sized
              sections; old path: one bounded, novelty-checked expansion). When the
              evidence is thin the shortfall is disclosed, never padded.
  * band   -- the expected range when the evidence supports it.
  * cap    -- the upper bound; the V2 planner scales section budgets to stay
              within it (words_cap).

No mode pads to reach a number: every page target is min(evidence, cap).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PagePolicy:
    floor: int
    band: tuple[int, int]
    cap: int
    words_cap: int           # V2 analysis-word ceiling that keeps the PDF within the cap


POLICIES = {
    "detailed": PagePolicy(floor=20, band=(25, 30), cap=32, words_cap=7_500),  # live G: 8,048 words + 6 charts = 34 pages
    "in_depth": PagePolicy(floor=60, band=(60, 100), cap=140, words_cap=36_000),  # live G: 39,124 words = 154 pages
}
WORDS_PER_PAGE = 380


def policy(tier: str | None) -> PagePolicy:
    return POLICIES.get(str(tier or "detailed").lower(), POLICIES["detailed"])


def evidence_page_target(tier: str | None, evidence_supported_words: int) -> dict:
    """Page target from the evidence: min(evidence pages, cap), flagged when below the floor."""
    p = policy(tier)
    evidence_pages = round(evidence_supported_words / WORDS_PER_PAGE, 1)
    target = min(evidence_pages, float(p.cap))
    return {"floor": p.floor, "band": list(p.band), "cap": p.cap, "evidencePages": evidence_pages,
            "target": round(target, 1), "belowFloor": evidence_pages < p.floor, "mode": "evidence_driven"}


def shortfall_note(tier: str | None, evidence_supported_words: int) -> str:
    """Methodology sentence disclosing a shortfall against the depth floor ('' when none)."""
    plan = evidence_page_target(tier, evidence_supported_words)
    if not plan["belowFloor"]:
        return ""
    return (f"The verified evidence supports about {plan['evidencePages']:.0f} pages of analysis, below this mode's "
            f"{plan['floor']}-page depth floor; the report was not padded to reach it.")


def length_report(tier: str | None, estimated_pages: float, evidence_supported_words: int, sections: list[dict]) -> dict:
    """How the written report compares with the mode's page band, and why when it falls short.

    ``sections``: [{"id", "words", "budget", "models", "ok"}]. A section is short when it has under 60% of its
    planned words. ``status``: within / above_cap / below_band_evidence (the evidence does not support more -
    disclosed, not padded) / below_band_writing (the evidence supported more: sections came back short or failed).
    """
    p = policy(tier)
    evidence_pages = round(evidence_supported_words / WORDS_PER_PAGE, 1)
    short = [s["id"] for s in sections if s.get("ok") and s.get("budget") and s.get("words", 0) < 0.6 * s["budget"]]
    failed = [s["id"] for s in sections if not s.get("ok")]
    models: dict[str, int] = {}
    for s in sections:
        for m in s.get("models") or []:
            models[m] = models.get(m, 0) + 1
    if estimated_pages > p.cap:
        status = "above_cap"
    elif estimated_pages >= p.band[0]:
        status = "within"
    elif evidence_pages and evidence_pages < p.band[0] and not (short or failed):
        status = "below_band_evidence"
    else:
        status = "below_band_writing"
    return {"tier": str(tier or "detailed"), "band": list(p.band), "floor": p.floor, "cap": p.cap,
            "estimatedPages": round(float(estimated_pages), 1), "evidencePages": evidence_pages, "status": status,
            "plannedWords": sum(int(s.get("budget") or 0) for s in sections),
            "writtenWords": sum(int(s.get("words") or 0) for s in sections if s.get("ok")),
            "shortSections": short, "failedSections": failed, "partsByModel": models}
