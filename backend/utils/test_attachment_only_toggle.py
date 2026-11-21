from utils.uploaded_workbook_analytics import resolve_attachment_only

DOCS = [{"name": "fund.pdf", "text": "Fund returns 12.4% in FY26"}]


def test_toggle_on_with_attachment_uses_only_the_attachment():
    assert resolve_attachment_only(True, "What are the trends?", "", DOCS) is True


def test_toggle_off_combines_attachment_with_research_even_for_summary_wording():
    # The old wording heuristic would have chosen attachment-only for "summarise"; the toggle wins.
    assert resolve_attachment_only(False, "Summarise this document", "[File: fund.pdf] ...", DOCS) is False


def test_toggle_on_without_attachment_runs_normal_research():
    assert resolve_attachment_only(True, "Analyse EV adoption in India", "", []) is False


def test_string_values_from_form_posts_are_understood():
    assert resolve_attachment_only("true", "x", "", DOCS) is True
    assert resolve_attachment_only("false", "Summarise this", "", DOCS) is False


def test_old_clients_without_the_flag_keep_the_wording_heuristic():
    assert resolve_attachment_only(None, "Summarise this document", "[File: fund.pdf] text", DOCS) is True
    assert resolve_attachment_only(None, "Summarise this document, search the web too", "[File: fund.pdf] text", DOCS) is False


def test_market_wide_investor_flow_questions_are_not_local_startup_discovery():
    from routes.report import _is_local_company_investor_discovery

    assert not _is_local_company_investor_discovery(
        "Produce a report with graphs on how the Indian share market performed over the last one month, "
        "and what share of the move came from foreign and domestic investor flows.")
    assert not _is_local_company_investor_discovery("FPI and DII flows into Indian equities in November")
    # genuine place-based discovery still is
    assert _is_local_company_investor_discovery("List software startups and angel investors in Electronic City, Bengaluru")
