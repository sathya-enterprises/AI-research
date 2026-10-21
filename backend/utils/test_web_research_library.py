import pytest
pytest.importorskip("psycopg2", reason="Install backend requirements to run PostgreSQL research-store tests")

from utils import research_store


def test_web_source_key_is_stable_for_same_url():
    a = research_store._web_source_key("https://Example.com/story/?utm_source=x")
    b = research_store._web_source_key("https://example.com/story")
    assert a == b
    assert a.startswith("WEB-")


def test_web_content_chunks_overlap_and_preserve_content():
    text = "A" * 2000 + " B " + "C" * 500
    chunks = research_store._web_content_chunks(text, chunk_size=1000, overlap=100)
    assert len(chunks) >= 3
    assert chunks[0].startswith("A")
    assert chunks[-1].endswith("C")
    assert any("B" in chunk for chunk in chunks)
