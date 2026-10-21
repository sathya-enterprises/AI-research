# Research store runtime data

`research.sqlite3` is created automatically at backend startup by
`utils.research_store.initialise()`.

The project distribution intentionally does not ship a live local research
SQLite database. Existing malformed local stores are automatically quarantined
with a timestamped `.corrupt-...` suffix and a fresh store is created so a
research request is not lost after spending several minutes on web retrieval.
