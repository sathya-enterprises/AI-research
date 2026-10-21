from routes.report import _dedupe_repeated_subsections, _dedupe_redundant_report_content


def test_paraphrased_subsection_is_removed_when_same_evidence_and_no_new_numbers():
    report = '''## Credit and Deposit Growth Dynamics

### Structural Credit-Deposit Divergence and Funding Mechanics
Credit demand has outpaced deposit mobilization, leaving banks to rely on certificates of deposit and short-term wholesale borrowings. Deposit growth was 13.47%, while credit growth reached 19.3% [S4], [S7]. Treasury desks use liquidity windows to smooth cash-flow mismatches.

### Other Topic
Cybersecurity controls continue to be an important operational priority for banks and financial institutions [S8].

### Structural Asymmetry Between Credit Expansion and Deposit Mobilization
Credit demand continues to run ahead of deposit collection. With credit growing at 19.3% and deposits at 13.47% [S4], [S7], banks rely on certificates of deposit and short-term wholesale borrowing, and treasury teams manage temporary cash-flow gaps through liquidity windows.
'''
    result = _dedupe_repeated_subsections(report)
    assert result.count("### Structural Credit-Deposit Divergence and Funding Mechanics") == 1
    assert result.count("### Structural Asymmetry Between Credit Expansion and Deposit Mobilization") == 0
    assert "### Other Topic" in result


def test_subsection_with_new_numeric_evidence_is_preserved():
    report = '''## Credit and Deposit Growth Dynamics

### Deposit Mobilization Strategy
Banks expect deposit costs to rise by 2% while credit demand grows 12% [S3]. A focus on granular retail deposits can support stability in funding.

### Deposit Mobilization Strategy
Banks expect deposit costs to rise by 4% while credit demand grows 12% [S3]. A focus on granular retail deposits can support stability in funding.
'''
    assert _dedupe_repeated_subsections(report).count("### Deposit Mobilization Strategy") == 2


def test_report_dedupe_is_idempotent():
    report = '''## Outlook

### Funding outlook
Credit is growing faster than deposits at 19.3% versus 13.47% [S3]. This places pressure on bank funding and liquidity.

### Funding outlook
Credit is growing faster than deposits at 19.3% versus 13.47% [S3]. This places pressure on bank funding and liquidity.
'''
    once = _dedupe_redundant_report_content(report)
    twice = _dedupe_redundant_report_content(once)
    assert once == twice


def test_duplicate_module_can_reuse_evidence_already_present_earlier_in_parent():
    report = '''## Credit and Deposit Growth Dynamics

### Deposit funding mechanics
Credit growth reached 19.3% as deposit growth stayed at 13.47% [S7]. Banks used certificates of deposit and wholesale borrowings to close the liquidity gap, while treasury teams monitored short-term cash mismatches.

### Credit trajectory
Annual credit growth was 16.08% and accelerated to 19.3% by July 2026 [S4]. Retail and MSME lending remained key contributors, while funding costs constrained margins.

### Structural credit-deposit divergence
Credit demand continued to exceed deposit mobilization. Deposit growth remained at 13.47% while credit growth reached 19.3% [S7]. Banks relied on certificates of deposit and short-term wholesale borrowing to bridge the same funding gap, with treasury teams managing temporary cash-flow mismatches.
'''
    result = _dedupe_repeated_subsections(report)
    assert result.count("### Structural credit-deposit divergence") == 0
    assert "### Deposit funding mechanics" in result
    assert "### Credit trajectory" in result
