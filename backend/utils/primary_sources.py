"""Domain-neutral detection of official / primary data publishers and data files.

The fetch stage needs to know which failed pages deserve a longer retry or a browser render, whatever the
topic. Instead of a per-topic list of hosts, this uses patterns that hold across countries and domains:
government and statistics TLDs (``.gov``, ``.gov.br``, ``.gouv.fr``, ``.go.jp``, ``.gc.ca``, ``.govt.nz``,
``.gob.mx``, ``.admin.ch``, ``.bund.de``, ``.int``, ``.mil``), Indian state portals (``.nic.in``,
``s3waas.gov.in``), parliament sites, and a short list of international data bodies that do not use
those TLDs. A topic's own official hosts can be added with ``GG_PRIMARY_HOSTS`` (comma separated).
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, urlparse

# second-level labels used by governments worldwide: <label>.<country> or <label> alone
_GOV_LABELS = ("gov", "gob", "gouv", "go", "govt", "gv", "gc", "admin", "bund", "government", "nic", "mil")
_GOV_RE = re.compile(r"(?:^|\.)(?:%s)\.[a-z]{2}$|(?:^|\.)(?:gov|mil|int)$" % "|".join(_GOV_LABELS))
# public data bodies that publish primary statistics outside government TLDs
_INTERNATIONAL = (
    "un.org", "worldbank.org", "imf.org", "oecd.org", "bis.org", "adb.org", "afdb.org", "iadb.org", "aiib.org",
    "iea.org", "irena.org", "opec.org", "ember-energy.org", "fao.org", "ilo.org", "unesco.org", "unicef.org",
    "unctad.org", "wto.org", "wipo.int", "undp.org", "unep.org", "unhcr.org", "who.int", "europa.eu",
    "ecb.europa.eu", "census.gov", "data.gov", "ourworldindata.org", "sansad.in", "parliament.uk",
)
# central banks, exchanges and market infrastructure that use ordinary TLDs
_MARKET_PRIMARY = (
    "rbi.org.in", "nseindia.com", "nseindia.in", "bseindia.com", "nsdl.co.in", "cdslindia.com", "amfiindia.com",
    "niftyindices.com", "bankofengland.co.uk", "federalreserve.gov", "boj.or.jp", "bundesbank.de", "banque-france.fr",
)
DATA_FILE_EXTENSIONS = (".pdf", ".xlsx", ".xlsm", ".xls", ".csv", ".ods")
SPREADSHEET_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv", ".ods")


def host_of(url: str) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").casefold().removeprefix("www.")
    except ValueError:
        return ""


def _env_hosts() -> tuple[str, ...]:
    return tuple(h.strip().casefold() for h in os.getenv("GG_PRIMARY_HOSTS", "").split(",") if h.strip())


def _matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_primary_host(host: str) -> bool:
    """True for government, statistics, parliament, central-bank/exchange and international data hosts."""
    host = str(host or "").casefold().removeprefix("www.")
    if not host:
        return False
    return bool(_GOV_RE.search(host)) or _matches(host, _INTERNATIONAL + _MARKET_PRIMARY + _env_hosts())


def is_primary(url: str) -> bool:
    return is_primary_host(host_of(url))


def file_extension(url: str, content_type: str = "") -> str:
    """'.pdf', '.xlsx', '.csv' ... from the URL path or the content type; '' for an ordinary page."""
    ctype = str(content_type or "").casefold()
    if "pdf" in ctype:
        return ".pdf"
    if "spreadsheetml" in ctype or "ms-excel" in ctype:
        return ".xlsx" if "spreadsheetml" in ctype else ".xls"
    if "text/csv" in ctype:
        return ".csv"
    if "opendocument.spreadsheet" in ctype:
        return ".ods"
    try:
        parts = urlparse(str(url or ""))
        path = parts.path.casefold()
    except ValueError:
        return ""
    for ext in DATA_FILE_EXTENSIONS:
        if path.endswith(ext):
            return ext
    # Data APIs often expose a format through a query parameter instead of a
    # filename (for example UNICEF SDMX's ?format=csv). Recognize only explicit
    # format/filename values so ordinary page parameters stay ordinary pages.
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key, value = key.casefold(), value.strip().casefold()
        if key in {"format", "output", "export", "extension", "ext", "type"}:
            compact = value.rsplit("/", 1)[-1].split(";", 1)[0].lstrip(".")
            candidate = f".{compact}"
            if candidate in DATA_FILE_EXTENSIONS:
                return candidate
        if key in {"file", "filename", "download", "attachment"}:
            for ext in DATA_FILE_EXTENSIONS:
                if value.endswith(ext):
                    return ext
    return ""


def is_data_file(url: str, content_type: str = "") -> bool:
    return bool(file_extension(url, content_type))


def fetch_priority(url: str, content_type: str = "") -> int:
    """0 = official data file, 1 = official page, 2 = other data file, 3 = anything else (lower goes first)."""
    primary, data = is_primary(url), is_data_file(url, content_type)
    if primary and data:
        return 0
    if primary:
        return 1
    if data:
        return 2
    return 3
