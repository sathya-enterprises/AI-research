from routes.report import _dedupe_repeated_subsections


def test_repeated_subsection_without_new_evidence_is_removed():
    report = '''## Regulation

### Liquidity Coverage Ratio and Funding Costs
Banks drew down liquidity buffers and now face higher funding costs as deposit growth lags credit demand [S3].

### Other Risks
Cybersecurity investment remains important for the sector [S4].

### Liquidity Coverage Ratio and Funding Costs
Banks drew down liquidity buffers and face higher funding costs as deposit growth lags credit demand [S3].
'''
    result = _dedupe_repeated_subsections(report)
    assert result.count("### Liquidity Coverage Ratio and Funding Costs") == 1
    assert "### Other Risks" in result


def test_repeated_subsection_with_new_numeric_evidence_is_preserved():
    report = '''### Deposit Mobilization
Banks expect funding costs to rise by 2% [S3].

### Deposit Mobilization
Banks expect funding costs to rise by 4% [S3].
'''
    result = _dedupe_repeated_subsections(report)
    assert result.count("### Deposit Mobilization") == 2
