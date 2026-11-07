# Long-running report status stability

In-Detail and Deep Research jobs execute in a dedicated worker thread with an isolated asyncio event loop. This prevents synchronous report work, including pagination/PDF preflight, from starving FastAPI status/health requests.

The frontend status poll treats transient proxy timeouts as retryable and validates that a `done` response contains actual report content before accepting it.
