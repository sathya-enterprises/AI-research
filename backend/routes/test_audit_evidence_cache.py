"""The cached source-evidence attachment returns exactly what the uncached computation returns."""
import json

from routes import report as R


def _inputs():
    manifest = R.build_source_registry([{"title": "Solar capacity", "url": "https://publisher.example/solar"}])
    row = {"title": "Solar capacity", "url": "https://publisher.example/solar", "pageFetchStatus": "ok",
           "pageFetchRelevanceMatched": True, "evidenceOrigin": "publisher_page",
           "pageContent": "TABLE 1 - Solar capacity\nPeriod | China (GW)\n2024 | 886.12"}
    return manifest, [row], "China solar capacity reached 886.12 GW in 2024 [S1]."


def _dump(value):
    return json.dumps(value, sort_keys=True, default=str)


def test_cached_result_equals_uncached_and_any_changed_input_recomputes():
    manifest, rows, text = _inputs()
    token = R._AUDIT_EVIDENCE_CACHE.set({})
    try:
        assert _dump(R._attach_audit_source_evidence(manifest, rows, text)) == _dump(
            R._attach_audit_source_evidence_uncached(manifest, rows, text))
        # a row flag, the report text and a context registry each change the key
        flipped = [dict(rows[0], pageFetchRelevanceMatched=False)]
        assert _dump(R._attach_audit_source_evidence(manifest, flipped, text)) == _dump(
            R._attach_audit_source_evidence_uncached(manifest, flipped, text))
        lines_token = R._CHART_EVIDENCE_LINES.set({"S1": ["Period | China (GW)", "2024 | 886.12"]})
        try:
            assert _dump(R._attach_audit_source_evidence(manifest, rows, text)) == _dump(
                R._attach_audit_source_evidence_uncached(manifest, rows, text))
        finally:
            R._CHART_EVIDENCE_LINES.reset(lines_token)
        # callers get copies: editing a result never changes the next one
        first = R._attach_audit_source_evidence(manifest, rows, text)
        first[0]["title"] = "edited by a caller"
        assert R._attach_audit_source_evidence(manifest, rows, text)[0]["title"] != "edited by a caller"
    finally:
        R._AUDIT_EVIDENCE_CACHE.reset(token)


def test_fact_sheet_is_built_once_per_store_and_rebuilt_when_passages_change():
    from types import SimpleNamespace
    from utils.fact_sheet import FactSheet, fact_sheet_for
    p1 = SimpleNamespace(passage_id="S1.1", text="Exports rose to 120.5 billion in 2024.", kind="narrative")
    store = SimpleNamespace(passages=[p1])
    first = fact_sheet_for(store)
    assert fact_sheet_for(store) is first  # reused, not rebuilt
    assert [f.value for f in first.facts] == [f.value for f in FactSheet([p1]).facts]
    store.passages.append(SimpleNamespace(passage_id="S2.1", text="Imports were 80.2 billion.", kind="narrative"))
    second = fact_sheet_for(store)
    assert second is not first and "80.2" in {f.value for f in second.facts}


def test_identical_report_html_is_rendered_once_and_any_change_renders_again(monkeypatch):
    from routes import pdf as P
    calls = []
    monkeypatch.setattr(P, "_pdf_with_chromium_uncached", lambda html: calls.append(html) or b"%PDF-" + str(len(calls)).encode())
    monkeypatch.setattr(P, "_PDF_RENDER_CACHE", {})
    big = "<html><body>" + "report text " * 6000 + "</body></html>"
    assert P._pdf_with_chromium(big) == P._pdf_with_chromium(big) and len(calls) == 1
    assert P._pdf_with_chromium(big + " ") != P._pdf_with_chromium(big) and len(calls) == 2
    small = "<html><body>short</body></html>"
    P._pdf_with_chromium(small); P._pdf_with_chromium(small)
    assert len(calls) == 4  # small documents always render
