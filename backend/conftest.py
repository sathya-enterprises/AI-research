"""Test-wide defaults for the backend suite."""
import os

# Report evidence reranking calls the RAG service. Unit tests never reach a real service: tests that cover
# reranking (utils/test_evidence_rerank.py) switch it on and pass a fake client.
os.environ.setdefault("GG_V2_RERANK", "0")
