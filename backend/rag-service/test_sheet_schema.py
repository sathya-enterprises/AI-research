"""Offline tests for spreadsheet schema extraction and retrieval targeting."""

from sheet_schema import extract_sheet_schema_chunks, matching_schema_chunk_indices


def test_extracts_complete_chart_price_schema():
    text = (
        "[Sheet: Company_Info]\nCompany Name,Ticker\nExample,EX\n\n"
        "[Sheet: Chart_Price]\n"
        "Date,Metric,Label,Value,Numeric Value,Chart,Query,Days,Extra_1\n"
        "2026-08-25,Price,Price on NSE,94.82,94.82,Price,Price-DMA50-DMA200-Volume,10000,"
    )
    schemas = extract_sheet_schema_chunks(text)
    chart_price = next(item for item in schemas if item["sheet_name"] == "Chart_Price")
    assert len(chart_price["columns"]) == 9
    assert chart_price["columns"] == [
        "Date", "Metric", "Label", "Value", "Numeric Value",
        "Chart", "Query", "Days", "Extra_1",
    ]
    assert "Column count: 9" in chart_price["text"]
    assert "Columns: Date | Metric | Label | Value | Numeric Value | Chart | Query | Days | Extra_1" in chart_price["text"]


def test_named_sheet_header_question_matches_schema_chunk():
    chunk = {
        "text": "[Workbook sheet schema]\nSheet: Chart_Price\nColumn count: 9\nColumns: Date | Metric",
        "source_metadata": {"rag_chunk_kind": "sheet_schema", "sheet_name": "Chart_Price"},
    }
    assert matching_schema_chunk_indices(
        "What columns are present in the Chart_Price sheet?", [chunk]
    ) == [0]


def test_schema_targeting_ignores_other_sheet_and_non_schema_queries():
    chunks = [{
        "text": "schema",
        "source_metadata": {"rag_chunk_kind": "sheet_schema", "sheet_name": "Chart_Price"},
    }]
    assert matching_schema_chunk_indices("What columns are in Ratios?", chunks) == []
    assert matching_schema_chunk_indices("How did the price change?", chunks) == []
