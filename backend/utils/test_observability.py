from utils import observability


def test_event_buffer_keeps_counts_and_timings_but_drops_raw_content():
    observability.clear_events()
    token = observability.set_trace_id("microtask-trace-0001")
    try:
        event = observability.record_event(
            "rag-client", "query", "ok", duration_ms=18.5,
            details={
                "query": "this must not be retained",
                "query_chars": 28,
                "context": "retrieved text must not be retained",
                "context_chars": 1200,
                "contextBuildMs": 3.2,
                "api_key": "must-not-leak",
                "rag_stages": {"pgvectorSearchMs": 2.1, "contextBuildMs": 3.2},
            },
        )
    finally:
        observability.reset_trace_id(token)

    assert event["traceId"] == "microtask-trace-0001"
    assert event["details"]["query_chars"] == 28
    assert event["details"]["context_chars"] == 1200
    assert event["details"]["contextBuildMs"] == 3.2
    assert "query" not in event["details"]
    assert "context" not in event["details"]
    assert "api_key" not in event["details"]
    assert event["details"]["rag_stages"] == {"pgvectorSearchMs": 2.1, "contextBuildMs": 3.2}


def test_event_buffer_is_bounded_and_newest_first():
    observability.clear_events()
    for index in range(510):
        observability.record_event("test", "tick", "ok", details={"sequence": index})
    recent = observability.recent_events(250)
    assert len(recent) == 250
    assert recent[0]["details"]["sequence"] == 509
    assert recent[-1]["details"]["sequence"] == 260
    observability.clear_events()
