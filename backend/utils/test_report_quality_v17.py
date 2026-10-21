from utils.report_quality import audit_report_structure


def test_conceptual_overlap_is_reported_conservatively():
    report = '''## Market Growth
India market growth is driven by consumer demand, household income, urbanization, retail expansion and digital adoption across major cities and regions while maintaining strong store productivity, channel economics, merchandising depth and geographic diversification across the organized retail market.

## Competitive Outlook
Competitive market growth is also driven by consumer demand, household income, urbanization, retail expansion and digital adoption across major cities and regions, with additional competition factors.
'''
    audit = audit_report_structure(report)
    assert 'conceptual_overlaps' in audit
    assert any(x['earlier'] == 'Market Growth' and x['later'] == 'Competitive Outlook' for x in audit['conceptual_overlaps'])


def test_short_sections_and_duplicate_headings_remain_supported():
    report = '## A\nshort\n\n## A\nshort\n'
    audit = audit_report_structure(report)
    assert audit['duplicate_headings']
    assert audit['short_sections']
