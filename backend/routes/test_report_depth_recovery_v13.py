"""V13 regressions: malformed provider JSON must not downgrade long report tiers."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.report import _strip_leaked_json_envelope_tail
from routes.html_report import _link_source_citations


def test_recovered_report_drops_leaked_presentation_json_tail():
    body = "## Methodology & Data Notes\n\n" + ("Evidence-backed methodology. " * 80)
    leaked = body + '\n\n","presentation":{"domain":"comparison","cover":{"enabled":false}}'
    cleaned = _strip_leaked_json_envelope_tail(leaked)
    assert cleaned.startswith("## Methodology & Data Notes")
    assert "presentation" not in cleaned
    assert len(cleaned) < len(leaked)


def test_parenthesized_source_citations_are_clickable():
    sources = [{"id": "S1", "title": "Test source", "url": "https://example.com"}]
    rendered = _link_source_citations("Supported by (S1) and [S1].", sources)
    assert rendered.count('data-source-id="S1"') == 2
    assert 'href="#source-S1"' in rendered


def test_long_tier_fallback_keeps_salvaged_body_without_a_page_quota():
    text = Path(ROOT / "routes" / "report.py").read_text(encoding="utf-8")
    assert 'salvaged_report if report_tier in ("detailed", "in_depth")' in text
    assert 'if report_tier in ("detailed", "in_depth"):' in text
    assert '"report":     salvaged_report' in text
    assert '"minimum": None' in text
    assert 'salvage final pages' not in text
