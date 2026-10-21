from utils.evidence_assets import extract_evidence_assets, validate_asset_uses


SOURCE = {
    "source_id": "S17",
    "url": "https://rbi.org.in/research/bank-cycle",
    "title": "Indian private-bank credit data",
}


def test_relevant_image_is_accepted_but_not_a_claim_source():
    assets = extract_evidence_assets(
        '<h2>Capital and asset quality</h2><figure><img src="/npa-chart.png" alt="Indian bank NPA trend chart" title="NPA trend"><figcaption>GNPA trend across Indian banks</figcaption></figure>',
        SOURCE,
        "Compare Indian private-bank historical credit cycles, NPA and capital trends",
        ["Asset quality", "Capital"],
    )
    assert len(assets) == 1
    asset = assets[0]
    assert asset.asset_type in {"chart", "figure", "image"}
    assert asset.can_use_as_evidence
    assert asset.source_id == "S17"
    assert asset.can_embed is False
    assert asset.relevance_score > 0.4


def test_decorative_and_promotional_images_are_rejected():
    assets = extract_evidence_assets(
        '<img src="/logo.png" alt="Company logo"><img src="/ad.png" alt="Advertisement - subscribe now">',
        SOURCE,
        "Compare Indian private-bank credit cycles",
    )
    assert len(assets) == 2
    assert all(a.evidence_role in {"decorative", "irrelevant"} for a in assets)
    assert all(not a.can_use_as_evidence for a in assets)


def test_table_is_structured_and_recreation_keeps_provenance():
    assets = extract_evidence_assets(
        '<h2>Bank comparison</h2><table><caption>NIM and deposit growth</caption><tr><th>Bank</th><th>NIM</th></tr><tr><td>Bank A</td><td>4.2%</td></tr></table>',
        SOURCE,
        "Compare Indian private-bank NIM, deposit growth and credit cycles",
        ["Bank comparison"],
    )
    table = next(a for a in assets if a.asset_type == "table")
    assert table.extracted_data["rows"][0] == ["Bank", "NIM"]
    assert table.can_recreate
    assert table.source_url == SOURCE["url"]


def test_claim_citation_and_visual_provenance_are_separate():
    assets = extract_evidence_assets(
        '<table><tr><th>Credit growth</th><th>Deposit growth</th></tr><tr><td>15%</td><td>10%</td></tr></table>',
        SOURCE,
        "Compare Indian private-bank credit growth and deposit growth",
    )
    asset = assets[0]
    uses = validate_asset_uses([{"asset_id": asset.asset_id, "use_asset": True, "presentation": "comparison_visual"}], [asset.to_dict()])
    assert uses[0]["source_id"] == "S17"
    assert uses[0]["source_url"] == SOURCE["url"]
    assert "S17" not in uses[0].get("claim_citation", "")


def test_chart_data_script_is_discovered_without_treating_it_as_a_claim():
    assets = extract_evidence_assets(
        '<h2>Credit growth</h2><script>const chart={labels:["2024","2025"],data:[10,15]};</script>',
        SOURCE,
        "Indian private-bank credit growth chart by year",
        ["Credit growth"],
    )
    assert assets[0].asset_type == "chart"
    assert assets[0].extracted_data["labels"] == ["2024", "2025"]
    assert assets[0].can_recreate is False
