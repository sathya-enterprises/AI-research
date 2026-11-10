"""Stage L: topic connectors - official pages that carry the core data series of a topic.

Search finds commentary; the series a report should chart (year-by-year FPI flows, monthly SIP inflows ...)
often sits on a regulator's or depository's own report page that search ranks low. A topic connector
is a small entry here: when the question matches ``trigger``, its official pages are put at the front
of the normal fetch queue. They are fetched, relevance-gated, table-extracted and verified like any other
page - no special scraper and no special trust. Adding a domain = adding one entry.

Disable with GG_TOPIC_SEEDS=0.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicConnector:
    name: str
    trigger: str            # regex on the question
    pages: tuple[tuple[str, str], ...]   # (url, title)


CONNECTORS: tuple[TopicConnector, ...] = (
    TopicConnector(
        "india_fpi_flows",
        r"(?i)\b(?:fii|fpi|foreign (?:institutional|portfolio) invest\w*)\b",
        (
            ("https://www.fpi.nsdl.co.in/web/Reports/ReportDetail.aspx?RepID=36", "NSDL: FPI investment details"),
            ("https://www.fpi.nsdl.co.in/web/Reports/Yearwise.aspx?RptType=6", "NSDL: FPI net investment, calendar year-wise"),
            ("https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx", "NSDL: latest FPI daily trends"),
        ),
    ),
    TopicConnector(
        "india_mutual_fund_flows",
        r"(?i)\b(?:sip|mutual fund|dii|domestic institutional)\b",
        (
            ("https://www.amfiindia.com/research-information/amfi-monthly", "AMFI: monthly mutual fund data"),
        ),
    ),
)


def seed_rows(question: str, *, existing_urls: set[str] | None = None, limit: int = 6) -> list[dict]:
    """Source rows (like search results) for every connector the question triggers."""
    if os.getenv("GG_TOPIC_SEEDS", "1").strip() == "0":
        return []
    seen = {u.rstrip("/").casefold() for u in (existing_urls or set())}
    rows: list[dict] = []
    for connector in CONNECTORS:
        if not re.search(connector.trigger, str(question or "")):
            continue
        for url, title in connector.pages:
            if url.rstrip("/").casefold() in seen:
                continue
            seen.add(url.rstrip("/").casefold())
            rows.append({"url": url, "title": title, "snippet": f"{title} (official data page)",
                         "provider": "topic_connector", "topicConnector": connector.name})
    return rows[:limit]
