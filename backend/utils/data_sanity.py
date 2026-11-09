"""Cross-checks on the fetched sector dataset before it becomes evidence (Stage I).

A value that fails a check is blocked (set to None, so no chart, table or sentence can use it) and
logged; the findings go into the research capture and the publication report.

  * constituent check: an index whose largest constituent group has its own index (Nifty Financial
    Services ~ banks) must not diverge from it by more than ``max_gap`` percentage points in the same
    period (QTD or quarter);
  * live-level check: a QTD close must agree with a live index level fetched in the same run (when one
    is available) within ``level_tolerance``;
  * range check: a quarterly return outside +/-``max_quarter`` % or a QTD return outside
    +/-``max_qtd`` % for a broad sector index is implausible.
"""
from __future__ import annotations

import logging

log = logging.getLogger("data_sanity")

# index -> its largest constituent group's own index (documented, configurable)
CONSTITUENT_PAIRS = {"NIFTY FINANCIAL SERVICES": "NIFTY BANK", "NIFTY PRIVATE BANK": "NIFTY BANK"}


def check_dataset_rows(rows: list[dict], *, live_levels: dict[str, float] | None = None, max_gap: float = 6.0,
                       level_tolerance: float = 0.04, max_quarter: float = 45.0, max_qtd: float = 30.0) -> list[dict]:
    findings: list[dict] = []
    by_label = {str(r.get("label") or "").upper(): r for r in rows if isinstance(r, dict)}

    def block_qtd(row, reason):
        findings.append({"index": row.get("label"), "value": f"QTD {row['qtd'].get('return')}%", "reason": reason})
        log.warning("Dataset sanity: blocked %s QTD %s%% - %s", row.get("label"), row["qtd"].get("return"), reason)
        row["qtd"] = None

    for label, row in by_label.items():
        qtd = row.get("qtd") if isinstance(row.get("qtd"), dict) else None
        if qtd and abs(float(qtd.get("return") or 0)) > max_qtd:
            block_qtd(row, f"outside +/-{max_qtd:g}% for a quarter to date")
            qtd = None
        level = (live_levels or {}).get(label)
        if qtd and level and qtd.get("close"):
            gap = abs(float(qtd["close"]) / float(level) - 1)
            if gap > level_tolerance:
                block_qtd(row, f"QTD close {qtd['close']} differs {gap:.1%} from the live index level {level}")
                qtd = None
        returns = row.get("returns") or []
        for i, value in enumerate(returns):
            if value is not None and abs(float(value)) > max_quarter:
                findings.append({"index": row.get("label"), "value": f"{(row.get('quarters') or ['?'] * len(returns))[i]} {value}%",
                                 "reason": f"outside +/-{max_quarter:g}% for one quarter"})
                returns[i] = None
    for index, group in CONSTITUENT_PAIRS.items():
        a, b = by_label.get(index), by_label.get(group)
        if not a or not b:
            continue
        qa = a.get("qtd") if isinstance(a.get("qtd"), dict) else None
        qb = b.get("qtd") if isinstance(b.get("qtd"), dict) else None
        if qa and qb and abs(float(qa["return"]) - float(qb["return"])) > max_gap:
            block_qtd(a, f"diverges {abs(float(qa['return']) - float(qb['return'])):.1f} pp from {group} "
                         f"({qb['return']}%), its largest constituent group")
        ra, rb = a.get("returns") or [], b.get("returns") or []
        for i, (x, y) in enumerate(zip(ra, rb)):
            if x is not None and y is not None and abs(float(x) - float(y)) > 2 * max_gap:
                findings.append({"index": a.get("label"), "value": f"{(a.get('quarters') or ['?'] * len(ra))[i]} {x}%",
                                 "reason": f"diverges {abs(float(x) - float(y)):.1f} pp from {group} ({y}%)"})
                ra[i] = None
    return findings
