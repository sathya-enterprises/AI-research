from pathlib import Path

ROOT = Path(__file__).parent
SRC = (ROOT / "rag_engine.py").read_text(encoding="utf-8")
REQ = (ROOT / "requirements.txt").read_text(encoding="utf-8")
ENV = (ROOT / ".env.example").read_text(encoding="utf-8")

assert "CrossEncoder" in SRC
assert "HF_TOKEN" in SRC
assert "HF_RERANK_MODEL" in SRC
assert "HF_RERANK_CANDIDATES" in SRC
assert "cross-encoder/ms-marco-MiniLM-L6-v2" in ENV
assert "transformers==4.44.2" in REQ
assert "retrieve_for_report" in SRC

# Report retrieval is query-relevant and reranked when the configured model is
# available. A reranker failure must retain embedding order and report status.
report_start = SRC.index("def retrieve_for_report")
report_end = SRC.index("# ── Scope detection", report_start)
report_block = SRC[report_start:report_end]
assert "candidate_count = min(sess.total, max(HF_RERANK_CANDIDATES * 2, 24))" in report_block
assert "self._reranker.predict(pairs, batch_size=HF_RERANK_BATCH_SIZE" in report_block
assert '"reranker_ran":  rerank_ran' in report_block
assert "retaining embedding order" in report_block

print("HF reranker configuration regression: PASS")
