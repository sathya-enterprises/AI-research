"""Offline replay of evidence reranking on saved captures, through the real RAG service /rerank endpoint.

For each capture: rebuild the evidence store and the run's outline leaves (as chart_funnel_replay.py does), run
utils.evidence_rerank.rerank_leaves against RAG_SERVICE_URL, then assign passages with and without the reranked
hits. Reports reranking time, pairs, fallbacks and how much of each section's evidence changed. No LLM calls.

usage: python scripts/rerank_overhead_replay.py CAPTURE.json [CAPTURE.json ...]
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)


def sections_from(outline: dict):
    from utils.report_outline import Section
    out = []
    for raw in outline.get("sections") or []:
        if raw.get("kind") != "analysis" or raw.get("status", "keep") != "keep":
            continue
        sec = Section(section_id=str(raw.get("id")), title=str(raw.get("title") or ""), goal=str(raw.get("goal") or ""),
                      queries=list(raw.get("queries") or []) or [str(raw.get("title") or "")],
                      entities=list(raw.get("entities") or []))
        for sub in raw.get("subsections") or []:
            if sub.get("status", "keep") == "keep":
                sec.subsections.append(Section(section_id=str(sub.get("id")), title=str(sub.get("title") or ""),
                                               goal=str(sub.get("goal") or ""),
                                               queries=list(sub.get("queries") or []) or [str(sub.get("title") or "")],
                                               entities=list(sub.get("entities") or [])))
        out.append(sec)
    return out


async def one(path: str) -> dict:
    import scripts.replay_capture as rc
    from scripts.topic_scorecard import _stage
    from utils.evidence_rerank import rerank_leaves
    from utils.evidence_store import build_evidence_store
    from utils.report_outline import TIER_RULES, assign_passages

    raw = json.load(open(path, encoding="utf-8"))
    stages = raw.get("stages") or raw
    final = _stage(stages, "final_output") or {}
    meta = _stage(stages, "request_metadata") or {}
    tier = str(meta.get("reportTier") or "detailed")
    rows, _ = rc.audit_source_rows(raw)
    store = build_evidence_store(rows, question=str(meta.get("question") or ""),
                                 manifest=[r for r in final.get("sources") or [] if isinstance(r, dict)])
    rc.reset_run_state()
    sections = sections_from((_stage(stages, "pipeline_v2") or {}).get("outline") or {})
    k_leaf = TIER_RULES.get(tier, TIER_RULES["detailed"])["k_leaf"]
    leaves = [leaf for sec in sections for leaf in sec.leaves()]
    t0 = time.perf_counter()
    hits, stats = await rerank_leaves(store, leaves, k=k_leaf * 2)
    wall = time.perf_counter() - t0
    plain, reranked = copy.deepcopy(sections), copy.deepcopy(sections)
    assign_passages(store, plain, k_leaf)
    assign_passages(store, reranked, k_leaf, leaf_hits=hits)
    a = [lf for s in plain for lf in s.leaves()]
    b = [lf for s in reranked for lf in s.leaves()]
    changed = [1 - len(set(x.passage_ids) & set(y.passage_ids)) / max(1, len(set(x.passage_ids) | set(y.passage_ids)))
               for x, y in zip(a, b)]
    sources = [len({store.get(p).source_id for p in y.passage_ids}) / max(1, len(y.passage_ids)) for y in b if y.passage_ids]
    sources0 = [len({store.get(p).source_id for p in x.passage_ids}) / max(1, len(x.passage_ids)) for x in a if x.passage_ids]
    return {"capture": os.path.basename(path)[17:41], "tier": tier, "passages": len(store.passages), "leaves": len(leaves),
            "reranked": stats["reranked"], "fallback": stats["fallbackReasons"], "pairs": stats["pairs"],
            "rerankSeconds": round(wall, 1), "secondsPerLeaf": round(wall / max(1, len(leaves)), 2),
            "serviceMs": stats["serviceMs"],
            "evidenceChanged": round(sum(changed) / max(1, len(changed)), 3),
            "sourceDiversityBefore": round(sum(sources0) / max(1, len(sources0)), 3),
            "sourceDiversityAfter": round(sum(sources) / max(1, len(sources)), 3)}


def main() -> None:
    os.environ["GG_V2_RERANK"] = "1"
    out = []
    for path in sys.argv[1:]:
        item = asyncio.run(one(path))
        out.append(item)
        print(json.dumps(item), flush=True)
    total = sum(i["rerankSeconds"] for i in out)
    leaves = sum(i["leaves"] for i in out)
    print(json.dumps({"runs": len(out), "leaves": leaves, "rerankSeconds": round(total, 1),
                      "secondsPerLeaf": round(total / max(1, leaves), 2)}))


if __name__ == "__main__":
    main()
