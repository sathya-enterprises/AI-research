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
    "detailed": PagePolicy(floor=20, band=(25, 30), cap=32, words_cap=10_500),
    "in_depth": PagePolicy(floor=40, band=(60, 130), cap=140, words_cap=52_000),
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
