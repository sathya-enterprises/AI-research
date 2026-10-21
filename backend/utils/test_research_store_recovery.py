import os
import pytest


@pytest.mark.skipif(
    not os.getenv("RESEARCH_DATABASE_URL") and not os.getenv("DATABASE_URL"),
    reason="Supabase/Postgres research store requires RESEARCH_DATABASE_URL or DATABASE_URL",
)
def test_research_store_uses_supabase_postgres():
    from utils import research_store
    research_store.initialise()
    assert research_store.STORE_BACKEND == "supabase_postgres"
