import os
import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("RESEARCH_TEST_DATABASE_URL"),
    reason="Set RESEARCH_TEST_DATABASE_URL to a disposable Supabase/Postgres database for integration tests",
)

# The historical SQLite-specific tests are intentionally not copied into the
# production test suite. Runtime integration tests should use a disposable
# Postgres database so they exercise the same persistence backend as production.
