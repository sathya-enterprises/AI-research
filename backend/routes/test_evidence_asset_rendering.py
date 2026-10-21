from routes.html_report import build_html_report


def test_selected_evidence_asset_renders_with_attribution():
    asset = {
        "asset_id": "VTEST1", "asset_type": "table", "title": "NIM comparison",
        "caption": "NIM comparison", "publisher": "rbi.org.in", "source_id": "S17",
        "source_url": "https://rbi.org.in/table", "can_recreate": True, "can_use_as_evidence": True,
        "extracted_data": {"rows": [["Bank", "NIM"], ["Bank A", "4.2%"]]},
    }
    html = build_html_report(
        "## Funding\n\n[ASSET:VTEST1]", "Test", "bank NIM", "", [], [], [],
        sources=[{"source_id": "S17", "title": "RBI", "url": "https://rbi.org.in/table"}],
        evidence_assets=[asset],
        asset_uses=[{"asset_id": "VTEST1", "use_asset": True, "presentation": "comparison_visual"}],
    )
    assert "NIM comparison" in html
    assert "Source: <a href=\"https://rbi.org.in/table\">rbi.org.in</a>" in html
    assert "Bank A" in html
