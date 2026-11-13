from utils.source_list import metadata_parts


def test_uploaded_file_metadata_does_not_repeat_filename_as_publisher():
    metadata = metadata_parts({
        "title": "PMSPRFFEB2026.xlsx",
        "publisher": "PMSPRFFEB2026.xlsx",
        "evidence_origin": "uploaded_workbook",
    })

    assert len(metadata) == 1
    assert metadata[0].startswith("User-provided workbook")
    assert "publisher-verified" in metadata[0]
