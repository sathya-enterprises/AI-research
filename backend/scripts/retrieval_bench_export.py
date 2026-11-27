"""Retrieval benchmark, step 1: export each saved run's evidence store and its real section queries.

For every capture with a V2 outline and a final report (the same set chart_funnel_replay.py uses), writes
<out>/<capture>.json with
  * passages: id, sourceId, kind, url, title, reliability, text (the evidence store rebuilt from the capture);
  * leaves: every outline leaf (subsection, or section without subsections) with the queries, entities and title
    that production retrieval searches (report_outline._leaf_hits);
  * lexical: for each leaf and query, the production ranking from EvidenceStore.search (BM25 x reliability x
    table boost), top 100 with scores - configuration A is this exact code, not a re-implementation;
  * cited: per section, the passage ids the final report cites.
No network and no LLM calls.

usage: python scripts/retrieval_bench_export.py OUT_DIR [--limit N]
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)


def _leaves(outline: dict) -> list[dict]:
    out = []
    for raw in outline.get("sections") or []:
        if raw.get("kind") != "analysis" or raw.get("status", "keep") != "keep":
            continue
        subs = [s for s in raw.get("subsections") or [] if s.get("status", "keep") == "keep"]
        for leaf in subs or [raw]:
            out.append({"leafId": str(leaf.get("id")), "sectionId": str(raw.get("id")),
                        "sectionTitle": str(raw.get("title") or ""), "title": str(leaf.get("title") or ""),
                        "goal": str(leaf.get("goal") or ""),
                        "queries": list(leaf.get("queries") or []) or [str(leaf.get("title") or "")],
                        "entities": list(leaf.get("entities") or [])})
    return out


def export(path: str, out_dir: Path) -> dict | None:
    import scripts.replay_capture as rc
    from scripts.topic_scorecard import _stage
    from utils.evidence_store import build_evidence_store

    raw = json.load(open(path, encoding="utf-8"))
    stages = raw.get("stages") or raw
    final = _stage(stages, "final_output") or {}
    meta = _stage(stages, "request_metadata") or {}
    outline = (_stage(stages, "pipeline_v2") or {}).get("outline") or {}
    manifest = [r for r in final.get("sources") or [] if isinstance(r, dict)]
    if not final.get("report") or not manifest or not outline.get("sections"):
        return None
    question = str(meta.get("question") or "")
    rows, _exact = rc.audit_source_rows(raw)
    t0 = time.perf_counter()
    store = build_evidence_store(rows, question=question, manifest=manifest)
    build_s = time.perf_counter() - t0
    rc.reset_run_state()
    leaves = _leaves(outline)
    if not leaves or len(store.passages) < 10:
        return None
    lexical, lex_seconds = {}, 0.0
    for leaf in leaves:
        per_query = {}
        for query in leaf["queries"] + ([" ".join(leaf["entities"])] if leaf["entities"] else []) + [leaf["title"]]:
            t0 = time.perf_counter()
            hits = store.search(query, k=100, max_per_source=100)
            lex_seconds += time.perf_counter() - t0
            per_query[query] = [[p.passage_id, s] for p, s in hits]
        lexical[leaf["leafId"]] = per_query
    report = str(final.get("report") or "")
    cited = {}
    for block in re.split(r"(?m)^## ", report):
        title = block.split("\n", 1)[0].strip()
        ids = re.findall(r"\b(S\d+\.\d+)\b", block)
        if ids:
            cited[title] = sorted(set(ids))
    name = Path(path).stem
    doc = {"capture": name, "question": question, "tier": str(meta.get("reportTier") or "detailed"),
           "storeBuildSeconds": round(build_s, 2), "lexicalQuerySeconds": round(lex_seconds, 3),
           "lexicalQueries": sum(len(v) for v in lexical.values()),
           "passages": [{"id": p.passage_id, "sourceId": p.source_id, "kind": p.kind, "url": p.url, "title": p.title,
                         "reliability": p.reliability, "text": p.text} for p in store.passages],
           "leaves": leaves, "lexical": lexical, "cited": cited}
    (out_dir / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return {"capture": name, "passages": len(store.passages), "leaves": len(leaves), "tier": doc["tier"]}


def main() -> None:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    files = sorted(f for f in glob.glob("diagnostics/research_capture_*.json") if "publication_report" not in f)
    done = []
    for path in files:
        try:
            item = export(path, out_dir)
        except Exception as exc:  # one broken capture must not stop the benchmark
            print(f"skip {Path(path).stem}: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        if item:
            done.append(item)
            print(f"{item['capture']}  passages={item['passages']}  leaves={item['leaves']}  {item['tier']}", flush=True)
        if limit and len(done) >= limit:
            break
    print(f"exported {len(done)} runs, {sum(d['passages'] for d in done)} passages, {sum(d['leaves'] for d in done)} leaves")


if __name__ == "__main__":
    main()
