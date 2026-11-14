from routes import report


def test_uploaded_file_evidence_merges_text_without_losing_or_duplicating_rows():
    token = report._UPLOADED_FILE_EVIDENCE_TEXTS.set({})
    try:
        documents = [{"name": "metrics.xlsx", "text": "[Sheet: A]\nNorth,10"}]
        first = report._record_uploaded_file_evidence(
            "metrics.xlsx", "[Sheet: B]\nSouth,20", documents,
        )
        report._record_uploaded_file_evidence("metrics.xlsx", "[Sheet: B]\nSouth,20", documents)

        evidence = report._UPLOADED_FILE_EVIDENCE_TEXTS.get()[report._normalize_filename("metrics.xlsx")]
        assert "[Sheet: A]\nNorth,10" in evidence
        assert evidence.count("[Sheet: B]\nSouth,20") == 1
        assert first["evidenceOrigin"] == "uploaded_workbook"
        assert first["pageFetchStatus"] == "not_applicable"
    finally:
        report._UPLOADED_FILE_EVIDENCE_TEXTS.reset(token)


def test_uploaded_evidence_gate_accepts_supported_file_values_and_rejects_other_subjects():
    source = {
        "id": "S1", "title": "metrics.xlsx", "kind": "Uploaded workbook",
        "evidenceOrigin": "uploaded_workbook",
        "_uploadedFileEvidenceExcerpt": (
            "Sheet: Performance | Row: 2 | Alpha Strategy | 1 Yr return (%) | 24.8%"
        ),
    }

    assert report._publisher_evidence_supports_metric(
        "24.8%", "Alpha Strategy 1 Year return", ["S1"], [source],
    )
    assert not report._publisher_evidence_supports_metric(
        "24.8%", "Beta Strategy 1 Yr return", ["S1"], [source],
    )
    assert not report._publisher_evidence_supports_metric(
        "25.8%", "Alpha Strategy 1 Yr return", ["S1"], [source],
    )
    assert not report._publisher_evidence_supports_metric(
        "24.8%", "Alpha Strategy 5 Year return", ["S1"], [source],
    )


def test_upload_source_keeps_file_provenance_without_publisher_authority():
    source = {
        "id": "S1", "title": "metrics.xlsx", "kind": "Uploaded workbook",
        "evidenceOrigin": "uploaded_workbook", "authority_tier": "primary_official",
        "reliability_score": 1.0,
    }

    merged = report._merge_source_evidence_statuses([source], [])[0]

    assert merged["evidence_origin"] == "uploaded_workbook"
    assert merged["page_fetch_status"] == "not_applicable"
    assert merged["authority_tier"] == "unknown"
    assert merged["reliability_score"] is None
    assert "not independently publisher-verified" in merged["reliability_note"]


def test_current_upload_rows_enter_the_passage_store_under_their_manifest_source_id():
    from utils.evidence_store import build_evidence_store

    name = "metrics.xlsx"
    token = report._UPLOADED_FILE_EVIDENCE_TEXTS.set({
        report._normalize_filename(name): (
            "[Sheet: Performance]\n"
            "| Fund | 1 Yr Return (%) |\n|---|---|\n| Alpha Growth | 24.8% |\n"
            "| Beta Balanced | 19.0% |\n"
        ),
    })
    try:
        manifest = [{
            "id": "S1", "title": name, "url": "",
            "evidence_origin": "uploaded_workbook", "page_fetch_status": "not_applicable",
            "chartEvidenceExcerpt": "Derived median return: 21.9% across 2 strategies.",
        }]
        documents = [{"name": name, "text": "fallback upload text"}]
        rows = report._uploaded_evidence_rows_for_store(manifest, documents)
        store = build_evidence_store(rows, question="1-year strategy return", manifest=manifest)

        assert rows[0]["_canonicalEvidenceSourceId"] == "S1"
        assert store.source_ids() == ["S1"]
        assert store.passages
        assert any("Alpha Growth" in passage.text for passage in store.passages)
        assert any("Derived median return" in passage.text for passage in store.passages)
        assert all(passage.kind == "calculated" for passage in store.passages)
    finally:
        report._UPLOADED_FILE_EVIDENCE_TEXTS.reset(token)
