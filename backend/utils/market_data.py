"""
Verified index-quote fetcher.

Mirrors the strategy already used by frontend/src/app/api/market/route.ts
(Yahoo Finance v8 chart endpoint, no auth required; Stooq CSV as a free
no-auth fallback) but as a small async Python helper the report pipeline can
call directly, so index numbers going into a report/chart come from a real
quote instead of whatever a web-search/Trendlyne snippet happened to contain.

If both providers fail for a symbol, it's simply omitted — callers should
treat this as best-effort and fall back to scraped sources when empty.
"""
import asyncio
import logging
import math
from typing import Optional

import httpx

log = logging.getLogger("market_data")

YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com/",
}

# stooq symbol, yahoo symbol, display label
INDEX_SYMBOLS = [
    ("^nsei", "^NSEI", "NIFTY 50"),
    ("^bsesn", "^BSESN", "SENSEX"),
    ("^nsebank", "^NSEBANK", "NIFTY BANK"),
    ("^cnxit", "^CNXIT", "NIFTY IT"),
    ("^nseminidcap50", "^NSEMDCP50", "NIFTY MIDCAP"),  # was ^CNXMID — not a real Yahoo ticker, always 404'd
]

# Additional NSE sectoral indices are fetched only for sector-rotation / sector
# history questions. Yahoo Finance tickers are best-effort; unavailable symbols
# are omitted and the report must disclose gaps rather than synthesizing returns.
SECTOR_INDEX_SYMBOLS = [
    ("^CNXAUTO", "NIFTY AUTO"),
    ("^CNXFMCG", "NIFTY FMCG"),
    ("^CNXMEDIA", "NIFTY MEDIA"),
    ("^CNXMETAL", "NIFTY METAL"),
    ("^CNXPHARMA", "NIFTY PHARMA"),
    ("^CNXPSUBANK", "NIFTY PSU BANK"),
    ("^NIFTYPVTBANK", "NIFTY PRIVATE BANK"),
    ("^CNXREALTY", "NIFTY REALTY"),
    ("^CNXENERGY", "NIFTY ENERGY"),
    ("^CNXFINANCE", "NIFTY FINANCIAL SERVICES"),
    ("^CNXCONSUMERDURABLES", "NIFTY CONSUMER DURABLES"),
    ("^CNXHEALTHCARE", "NIFTY HEALTHCARE"),
]


async def _yf_chart_quote(client: httpx.AsyncClient, yf_symbol: str) -> Optional[dict]:
    """Yahoo Finance v8 chart endpoint — no crumb/cookie needed."""
    try:
        r = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}",
            params={"interval": "1d", "range": "1d"},
            headers=YF_HEADERS,
            timeout=8.0,
        )
        if r.status_code != 200:
            return None
        meta = (r.json().get("chart", {}).get("result") or [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        if not price:
            return None
        prev = meta.get("chartPreviousClose", price)
        change_pct = ((price - prev) / prev * 100) if prev else 0.0
        return {"price": price, "changePct": change_pct}
    except Exception as e:
        log.debug("yf_chart_quote failed for %s: %s", yf_symbol, e)
        return None


async def _stooq_quote(client: httpx.AsyncClient, stooq_symbol: str) -> Optional[dict]:
    """Stooq CSV — free, no auth, reliable fallback when Yahoo rate-limits."""
    try:
        r = await client.get(
            "https://stooq.com/q/l/",
            params={"s": stooq_symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        lines = r.text.strip().splitlines()
        if len(lines) < 2:
            return None
        cols = lines[1].split(",")
        close, open_ = float(cols[6]), float(cols[4])
        if not close or not open_:
            return None
        return {"price": close, "changePct": (close - open_) / open_ * 100}
    except Exception as e:
        log.debug("stooq_quote failed for %s: %s", stooq_symbol, e)
        return None


async def _yf_chart_history(
    client: httpx.AsyncClient, yf_symbol: str, range_str: str, interval: str,
) -> Optional[list[dict]]:
    """Yahoo Finance v8 chart endpoint, same as _yf_chart_quote but requesting
    a wider range/interval (e.g. range=2y&interval=3mo) so we get a real
    series of period-end closes instead of a single day's price. Yahoo's
    documented intraday/interval values include 3mo, so an ~8-quarter window
    maps naturally onto range=2y&interval=3mo — each bucket is one point.
    """
    try:
        r = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}",
            params={"interval": interval, "range": range_str},
            headers=YF_HEADERS,
            timeout=10.0,
        )
        if r.status_code != 200:
            return None
        result = (r.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []

        points: list[dict] = []
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            # Label by the bucket's start month/year rather than inventing a
            # fiscal-quarter number (e.g. "Q2 FY25") — India's fiscal year
            # (Apr-Mar) means a naive Q1/Q2/Q3/Q4 label could easily be wrong
            # by one quarter depending on where the 2y window starts. A plain
            # "Jul 2024"-style label is unambiguous and lets the report
            # writer map it onto whatever quarter framing the question used.
            from datetime import datetime, timezone
            label = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d" if interval == "1d" else "%b %Y")
            points.append({"date": label, "close": round(float(close), 2)})
        return points or None
    except Exception as e:
        log.debug("yf_chart_history failed for %s: %s", yf_symbol, e)
        return None


async def _yf_chart_ohlc(
    client: httpx.AsyncClient, yf_symbol: str, range_str: str, interval: str,
) -> Optional[list[dict]]:
    """Like _yf_chart_history, but keeps the full open/high/low/close for each
    bar instead of collapsing to just the close — needed for "daily price
    action" / "OHLC for each session" style questions that _yf_chart_history
    (period-end closes only) can't answer.
    """
    try:
        r = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}",
            params={"interval": interval, "range": range_str},
            headers=YF_HEADERS,
            timeout=10.0,
        )
        if r.status_code != 200:
            return None
        result = (r.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens  = quote.get("open") or []
        highs  = quote.get("high") or []
        lows   = quote.get("low") or []
        closes = quote.get("close") or []

        from datetime import datetime, timezone
        bars: list[dict] = []
        for i, ts in enumerate(timestamps):
            o = opens[i]  if i < len(opens)  else None
            h = highs[i]  if i < len(highs)  else None
            l = lows[i]   if i < len(lows)   else None
            c = closes[i] if i < len(closes) else None
            if None in (o, h, l, c):
                continue  # incomplete bar (e.g. today's still-forming session)
            bars.append({
                "date":  datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %Y"),
                "open":  round(float(o), 2),
                "high":  round(float(h), 2),
                "low":   round(float(l), 2),
                "close": round(float(c), 2),
            })
        return bars or None
    except Exception as e:
        log.debug("yf_chart_ohlc failed for %s: %s", yf_symbol, e)
        return None


async def fetch_index_daily_ohlc(range_str: str = "5d", interval: str = "1d") -> list[dict]:
    """Returns real per-session OHLC bars for major Indian indices, e.g.:
        [{"label": "NIFTY 50", "bars": [{"date": "01 Sep 2026", "open": ...,
                                          "high": ..., "low": ..., "close": ...}, ...]}, ...]
    Default range/interval (5d/1d) covers "last week of trading" style asks.
    Yahoo-only, same best-effort contract as the other fetchers here.
    """
    out: list[dict] = []
    async with httpx.AsyncClient() as client:
        for _, yf_sym, label in INDEX_SYMBOLS:
            bars = await _yf_chart_ohlc(client, yf_sym, range_str, interval)
            if bars:
                out.append({"label": label, "bars": bars})
    log.info("market_data: fetched daily OHLC for %d/%d indices (range=%s, interval=%s)",
              len(out), len(INDEX_SYMBOLS), range_str, interval)
    return out


def format_ohlc_history_as_source(series: list[dict]) -> Optional[dict]:
    """Wraps a fetch_index_daily_ohlc() series as a synthetic, high-trust
    "source" dict — same shape/contract as format_quotes_as_source() below."""
    if not series:
        return None
    lines = []
    for s in series:
        for bar in s["bars"]:
            lines.append(
                f"{s['label']} — {bar['date']}: Open {bar['open']:.2f}, High {bar['high']:.2f}, "
                f"Low {bar['low']:.2f}, Close {bar['close']:.2f}"
            )
    return {
        "title": (
            "VERIFIED DAILY OHLC (authoritative — real per-session open/high/low/close "
            "levels from Yahoo Finance; use these exact figures, one line per trading "
            "session, do not invent or estimate any figure not listed here)"
        ),
        "url": "internal://verified-daily-ohlc",
        "snippet": " | ".join(lines)[:2000],
        "fullContent": "\n".join(lines),
    }


async def fetch_historical_index_quotes(
    range_str: str = "2y", interval: str = "3mo",
) -> list[dict]:
    """Returns a real historical close series per major Indian index, e.g.:
        [{"label": "NIFTY 50", "points": [{"date": "Jul 2024", "close": 24812.35}, ...]}, ...]
    Yahoo-only — Stooq's free endpoint only gives the latest single quote, not
    a clean multi-point history, so it isn't used here as a fallback the way
    it is in fetch_index_quotes(). If Yahoo fails for a symbol (or returns
    fewer than 2 usable points, which isn't enough to show a trend), that
    symbol is simply omitted — same best-effort contract as the live fetcher.
    """
    out: list[dict] = []
    async with httpx.AsyncClient() as client:
        for _, yf_sym, label in INDEX_SYMBOLS:
            points = await _yf_chart_history(client, yf_sym, range_str, interval)
            if points and len(points) >= 2:
                out.append({"label": label, "points": points})
    log.info("market_data: fetched historical series for %d/%d indices (range=%s, interval=%s)",
              len(out), len(INDEX_SYMBOLS), range_str, interval)
    return out


async def fetch_historical_sector_index_quotes(
    range_str: str = "3y", interval: str = "3mo", concurrency: int = 5,
) -> list[dict]:
    """Fetch best-effort quarterly history for Indian sectoral indices in parallel.

    A three-year request allows the caller to select the latest eight quarters
    even when the provider omits an initial bucket. The returned observations
    are raw period-end closes; returns are calculated by the formatter, never
    generated from narrative text.
    """
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    async with httpx.AsyncClient() as client:
        async def fetch_one(symbol: str, label: str) -> dict | None:
            async with semaphore:
                points = await _yf_chart_history(client, symbol, range_str, interval)
            if points and len(points) >= 2:
                return {"label": label, "points": points}
            log.debug("market_data: historical sector index unavailable: %s (%s)", label, symbol)
            return None
        results = await asyncio.gather(*[
            fetch_one(symbol, label) for symbol, label in SECTOR_INDEX_SYMBOLS
        ], return_exceptions=True)
    out = [x for x in results if isinstance(x, dict)]
    log.info("market_data: fetched historical sector series for %d/%d indices (range=%s, interval=%s)",
             len(out), len(SECTOR_INDEX_SYMBOLS), range_str, interval)
    return out


def format_historical_quotes_as_source(series: list[dict]) -> Optional[dict]:
    """Wraps a fetched historical series as a synthetic, high-trust "source"
    dict — same shape/contract as format_quotes_as_source() below, but for
    period-over-period data instead of a single day's snapshot."""
    if not series:
        return None
    lines = []
    for s in series:
        pts = s["points"]
        parts = [f"{pts[0]['date']}: {pts[0]['close']:.2f}"]
        for prev, cur in zip(pts, pts[1:]):
            chg = ((cur["close"] - prev["close"]) / prev["close"] * 100) if prev["close"] else 0.0
            parts.append(f"{cur['date']}: {cur['close']:.2f} ({chg:+.2f}% vs prior period)")
        lines.append(f"{s['label']} — " + "; ".join(parts))
    return {
        "title": (
            "VERIFIED HISTORICAL INDEX DATA (authoritative — real period-end closing "
            "levels from Yahoo Finance, use these exact figures for any quarter-over-quarter "
            "or period comparison; each period is an ~3-month bucket labelled by its start "
            "month — map it to a fiscal quarter in prose if needed, but do not alter the "
            "underlying figures)"
        ),
        "url": "internal://verified-historical-index-quotes",
        "snippet": " | ".join(lines)[:2000],
        "fullContent": "\n".join(lines),
    }


async def fetch_index_quotes() -> list[dict]:
    """Returns verified quotes for the major Indian indices, e.g.:
        [{"label": "NIFTY 50", "price": 24812.35, "changePct": 0.42}, ...]
    Tries Yahoo first, falls back to Stooq per-symbol. Silently omits any
    symbol both providers fail on rather than raising.
    """
    out: list[dict] = []
    async with httpx.AsyncClient() as client:
        for stooq_sym, yf_sym, label in INDEX_SYMBOLS:
            quote = await _yf_chart_quote(client, yf_sym)
            if not quote:
                quote = await _stooq_quote(client, stooq_sym)
            if quote:
                out.append({"label": label, **quote})
    log.info("market_data: fetched %d/%d index quotes", len(out), len(INDEX_SYMBOLS))
    return out


async def _yf_symbol_search(client: httpx.AsyncClient, company_name: str) -> Optional[dict]:
    """Resolve a free-text company name (e.g. 'Reliance Industries') to a real
    ticker via Yahoo's unauthenticated symbol-search endpoint. This is what
    lets stock-fundamentals lookup generalize to ANY company mentioned in a
    prompt instead of only a hard-coded list — same no-crumb-needed contract
    as the chart endpoint above.
    """
    try:
        r = await client.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": company_name, "quotesCount": 5, "newsCount": 0},
            headers=YF_HEADERS,
            timeout=8.0,
        )
        if r.status_code != 200:
            return None
        for q in r.json().get("quotes", []):
            # EQUITY only — skip ETFs/indices/crypto/futures matches so a name
            # like "Tata Motors" doesn't resolve to some unrelated fund.
            if q.get("quoteType") == "EQUITY" and q.get("symbol"):
                return {"symbol": q["symbol"], "name": q.get("shortname") or q.get("longname") or company_name}
        return None
    except Exception as e:
        log.debug("yf_symbol_search failed for %r: %s", company_name, e)
        return None


_yf_crumb_cache: dict[str, str] = {}  # "crumb" -> value; module-level, one process-lifetime crumb is enough


async def _get_yf_crumb(client: httpx.AsyncClient) -> Optional[str]:
    """quoteSummary (unlike the v8 chart endpoint) has required a
    cookie + crumb pair since Yahoo locked it down in 2024 — calling it with
    just a User-Agent now gets an unconditional 401, which is exactly what
    was happening here before (every /v10/finance/quoteSummary/... call
    failing with 401, so market_data always resolved 0 fundamentals).

    Fetches an A1/A3 session cookie from the public finance.yahoo.com page,
    then exchanges it for a crumb via the getcrumb endpoint. Both are cached
    on the client's cookie jar / this module for the process lifetime — the
    crumb doesn't need to be re-fetched per request.
    """
    if _yf_crumb_cache.get("crumb"):
        return _yf_crumb_cache["crumb"]
    try:
        # Populates client.cookies with the session cookie Yahoo expects.
        await client.get("https://fc.yahoo.com", headers=YF_HEADERS, timeout=8.0)
        r = await client.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            headers=YF_HEADERS,
            timeout=8.0,
        )
        crumb = r.text.strip()
        if r.status_code == 200 and crumb and "<html" not in crumb.lower():
            _yf_crumb_cache["crumb"] = crumb
            return crumb
    except Exception as e:
        log.debug("yf_get_crumb failed: %s", e)
    return None


async def _yf_quote_summary(client: httpx.AsyncClient, symbol: str) -> Optional[dict]:
    """Pulls valuation/debt/profitability fundamentals for one resolved
    ticker via the v10 quoteSummary endpoint (price + summaryDetail +
    defaultKeyStatistics + financialData modules). Best-effort: any missing
    field is simply omitted rather than faked, same contract as the rest of
    this module.
    """
    try:
        params = {"modules": "price,summaryDetail,defaultKeyStatistics,financialData"}
        crumb = await _get_yf_crumb(client)
        if crumb:
            params["crumb"] = crumb
        r = await client.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            params=params,
            headers=YF_HEADERS,
            timeout=8.0,
        )
        if r.status_code == 401 and crumb:
            # Crumb may have gone stale (session cookie expired) — refetch
            # once and retry rather than giving up on every symbol for the
            # rest of the process lifetime.
            _yf_crumb_cache.pop("crumb", None)
            crumb = await _get_yf_crumb(client)
            if crumb:
                params["crumb"] = crumb
                r = await client.get(
                    f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
                    params=params,
                    headers=YF_HEADERS,
                    timeout=8.0,
                )
        if r.status_code != 200:
            return None
        result = (r.json().get("quoteSummary", {}).get("result") or [None])[0]
        if not result:
            return None

        def raw(module: str, field: str):
            v = (result.get(module, {}) or {}).get(field, {})
            return v.get("raw") if isinstance(v, dict) else None

        price = result.get("price", {}) or {}
        out = {
            "symbol": symbol,
            "name": price.get("longName") or price.get("shortName") or symbol,
            "currency": price.get("currency"),
            "price": raw("price", "regularMarketPrice"),
            "changePct": raw("price", "regularMarketChangePercent"),
            "marketCap": raw("price", "marketCap"),
            "peRatio": raw("summaryDetail", "trailingPE") or raw("defaultKeyStatistics", "trailingPE"),
            "forwardPE": raw("summaryDetail", "forwardPE"),
            "priceToBook": raw("defaultKeyStatistics", "priceToBook"),
            "debtToEquity": raw("financialData", "debtToEquity"),
            "returnOnEquityPct": (
                raw("financialData", "returnOnEquity") * 100
                if raw("financialData", "returnOnEquity") is not None else None
            ),
            "profitMarginPct": (
                raw("financialData", "profitMargins") * 100
                if raw("financialData", "profitMargins") is not None else None
            ),
            "revenueGrowthPct": (
                raw("financialData", "revenueGrowth") * 100
                if raw("financialData", "revenueGrowth") is not None else None
            ),
            "earningsGrowthPct": (
                raw("financialData", "earningsGrowth") * 100
                if raw("financialData", "earningsGrowth") is not None else None
            ),
            "epsTTM": raw("defaultKeyStatistics", "trailingEps"),
            "dividendYieldPct": (
                raw("summaryDetail", "dividendYield") * 100
                if raw("summaryDetail", "dividendYield") is not None else None
            ),
            "fiftyTwoWeekHigh": raw("summaryDetail", "fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": raw("summaryDetail", "fiftyTwoWeekLow"),
            "totalCashPerShare": raw("financialData", "totalCashPerShare"),
        }
        # Require at least price + one fundamental field, otherwise this
        # symbol adds no value over a plain price quote and should be
        # treated as a miss so callers can skip it.
        has_fundamental = any(
            out[k] is not None for k in ("peRatio", "debtToEquity", "returnOnEquityPct", "marketCap")
        )
        if out["price"] is None or not has_fundamental:
            return None
        return out
    except Exception as e:
        log.debug("yf_quote_summary failed for %s: %s", symbol, e)
        return None


async def fetch_stock_fundamentals(company_names: list[str]) -> list[dict]:
    """Resolves each free-text company name to a ticker and pulls verified
    valuation/debt/profitability fundamentals for it. Best-effort per name —
    a company that fails to resolve or has no fundamentals data is simply
    omitted, never faked. Caps at 6 names so a noisy extraction upstream
    can't turn into a slow report generation call.
    """
    out: list[dict] = []
    seen_symbols: set[str] = set()
    async with httpx.AsyncClient() as client:
        for name in company_names[:6]:
            match = await _yf_symbol_search(client, name)
            if not match or match["symbol"] in seen_symbols:
                continue
            data = await _yf_quote_summary(client, match["symbol"])
            if data:
                seen_symbols.add(match["symbol"])
                out.append(data)
    log.info("market_data: resolved fundamentals for %d/%d company name(s)", len(out), len(company_names))
    return out


def format_stock_fundamentals_as_source(stocks: list[dict]) -> Optional[dict]:
    """Wraps fetched per-company fundamentals as a synthetic, high-trust
    source dict, same shape/contract as format_quotes_as_source() above."""
    if not stocks:
        return None
    lines = []
    for s in stocks:
        parts = [f"{s['name']} ({s['symbol']})"]
        if s.get("price") is not None:
            chg = f" ({s['changePct']:+.2f}%)" if s.get("changePct") is not None else ""
            parts.append(f"price {s['price']:.2f} {s.get('currency') or ''}{chg}".strip())
        if s.get("marketCap") is not None:
            parts.append(f"market cap {s['marketCap']:,.0f}")
        if s.get("peRatio") is not None:
            parts.append(f"trailing P/E {s['peRatio']:.2f}x")
        if s.get("forwardPE") is not None:
            parts.append(f"forward P/E {s['forwardPE']:.2f}x")
        if s.get("priceToBook") is not None:
            parts.append(f"P/B {s['priceToBook']:.2f}x")
        if s.get("debtToEquity") is not None:
            parts.append(f"debt-to-equity {s['debtToEquity']:.2f}")
        if s.get("returnOnEquityPct") is not None:
            parts.append(f"ROE {s['returnOnEquityPct']:.2f}%")
        if s.get("profitMarginPct") is not None:
            parts.append(f"net margin {s['profitMarginPct']:.2f}%")
        if s.get("revenueGrowthPct") is not None:
            parts.append(f"revenue growth (YoY) {s['revenueGrowthPct']:.2f}%")
        if s.get("earningsGrowthPct") is not None:
            parts.append(f"earnings growth (YoY) {s['earningsGrowthPct']:.2f}%")
        if s.get("epsTTM") is not None:
            parts.append(f"EPS (TTM) {s['epsTTM']:.2f}")
        if s.get("dividendYieldPct") is not None:
            parts.append(f"dividend yield {s['dividendYieldPct']:.2f}%")
        if s.get("fiftyTwoWeekHigh") is not None and s.get("fiftyTwoWeekLow") is not None:
            parts.append(f"52-week range {s['fiftyTwoWeekLow']:.2f}-{s['fiftyTwoWeekHigh']:.2f}")
        lines.append(" | ".join(parts))
    return {
        "title": (
            "VERIFIED LIVE STOCK FUNDAMENTALS (authoritative, sourced directly from exchange "
            "data — use these exact figures for P/E, market cap, debt-to-equity, ROE, margins, "
            "growth rates, EPS, dividend yield, and 52-week range for these companies; do not "
            "substitute a vaguer 'typical'/'historical range' figure from another source when a "
            "verified number is listed here for the same company and metric)"
        ),
        "url": "internal://verified-stock-fundamentals",
        "snippet": " || ".join(lines)[:2000],
        "fullContent": "\n".join(lines),
    }




async def _niftyindices_month_end_closes(
    client: httpx.AsyncClient, index_name: str, start_month: tuple[int, int], end_month: tuple[int, int],
) -> dict[tuple[int, int], float]:
    """Fetch official Nifty Indices daily closes and reduce them to month-end closes.

    This is a fallback for Yahoo ticker aliases that return 404 or have gaps. The
    official endpoint is queried in <= 360-day windows and only observed closes
    are returned; missing values are never interpolated. Failure is best-effort.
    """
    from calendar import monthrange
    from datetime import date, datetime, timedelta
    import json

    base_url = "https://www.niftyindices.com/Backpage.aspx/getHistoricaldatatabletoString"
    month_start = date(start_month[0], start_month[1], 1)
    month_end = date(end_month[0], end_month[1], monthrange(end_month[0], end_month[1])[1])
    start_date = month_start
    daily: dict[tuple[int, int], tuple[date, float]] = {}
    headers = {
        "User-Agent": YF_HEADERS["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json; charset=utf-8",
        "Origin": "https://www.niftyindices.com",
        "Referer": "https://www.niftyindices.com/reports/historical-data",
    }
    while start_date <= month_end:
        end_date = min(start_date + timedelta(days=359), month_end)
        cinfo = (
            "{'name':'" + index_name.replace("'", "") + "','startDate':'" + start_date.strftime("%d %b %Y")
            + "','endDate':'" + end_date.strftime("%d %b %Y") + "','indexName':'"
            + index_name.replace("'", "") + "'}"
        )
        try:
            response = await client.post(base_url, json={"cinfo": cinfo}, headers=headers, timeout=15.0)
            if response.status_code == 200:
                body = response.json()
                payload = body.get("d") if isinstance(body, dict) else None
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (json.JSONDecodeError, TypeError):
                        payload = []
                if isinstance(payload, list):
                    for item in payload:
                        if not isinstance(item, dict):
                            continue
                        raw_date = next((item.get(k) for k in ("HistoricalDate", "DATE", "Date", "date") if item.get(k)), None)
                        raw_close = next((item.get(k) for k in ("CLOSE", "Close", "close", "Closing") if item.get(k) is not None), None)
                        parsed_date = None
                        for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d %B %Y"):
                            try:
                                parsed_date = datetime.strptime(str(raw_date).strip(), fmt).date()
                                break
                            except (ValueError, TypeError):
                                continue
                        try:
                            value = float(str(raw_close).replace(",", "").strip())
                        except (ValueError, TypeError):
                            continue
                        if parsed_date and value > 0:
                            key = (parsed_date.year, parsed_date.month)
                            previous = daily.get(key)
                            if previous is None or parsed_date > previous[0]:
                                daily[key] = (parsed_date, value)
            else:
                log.warning("market_data: Nifty Indices historical fallback HTTP %s for %s", response.status_code, index_name)
        except Exception as exc:
            log.warning("market_data: Nifty Indices historical fallback failed for %s: %s", index_name, exc)
        start_date = end_date + timedelta(days=1)
    return {key: value for key, (_, value) in daily.items()}


async def fetch_sector_quarterly_returns(quarters: int = 8, concurrency: int = 5) -> list[dict]:
    """Calculate completed calendar-quarter returns from month-end index closes.

    Yahoo's daily `range=3y` endpoint has intermittently returned incomplete
    history for NSE indices. Monthly bars contain the last trading close for
    each month and are sufficient for calendar-quarter returns because each
    quarter ends in March, June, September, or December. We select those
    month-end bars explicitly; we never interpolate or invent missing values.
    """
    from datetime import datetime, timezone
    from collections import defaultdict

    now = datetime.now(timezone.utc)
    current_q = (now.month - 1) // 3
    last_completed_month = current_q * 3
    if last_completed_month == 0:
        end_year, end_month = now.year - 1, 12
    else:
        end_year, end_month = now.year, last_completed_month

    ends = []
    y, m = end_year, end_month
    for _ in range(max(quarters, 1) + 1):
        ends.append((y, m))
        m -= 3
        if m <= 0:
            y -= 1
            m += 12
    ends.reverse()
    quarter_labels = [f"Q{((month - 1) // 3) + 1} {year}" for year, month in ends]

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    async with httpx.AsyncClient() as client:
        async def fetch_one(symbol: str, label: str):
            async with semaphore:
                points = await _yf_chart_history(client, symbol, "5y", "1mo")
            history_sources = ["Yahoo Finance monthly bars"] if points else []
            official_recovered = 0
            if not points:
                log.warning("market_data: no monthly history for sector %s (%s); trying official Nifty Indices history", label, symbol)
                month_closes: dict[tuple[int, int], float] = {}
            else:
                month_closes = {}

            # The Yahoo timestamp identifies the month represented by the bar;
            # its close is the last available trading close for that month.
            if points:
                for point in points:
                    raw_date = str(point.get("date") or "").strip()
                    parsed = None
                    for fmt in ("%b %Y", "%Y-%m-%d", "%B %Y"):
                        try:
                            parsed = datetime.strptime(raw_date, fmt)
                            break
                        except ValueError:
                            continue
                    if parsed is None:
                        continue
                    try:
                        month_closes[(parsed.year, parsed.month)] = float(point["close"])
                    except (TypeError, ValueError, KeyError):
                        continue

            missing_months = [(year, month) for year, month in ends if (year, month) not in month_closes]
            if missing_months:
                official_closes = await _niftyindices_month_end_closes(
                    client, label, ends[0], ends[-1],
                )
                recovered = 0
                for key in missing_months:
                    if key in official_closes:
                        month_closes[key] = official_closes[key]
                        recovered += 1
                if recovered:
                    official_recovered = recovered
                    history_sources.append("official Nifty Indices daily history")
                    log.info("market_data: recovered %d missing quarter-end closes for %s from official Nifty Indices history", recovered, label)

            closes = [month_closes.get((year, month)) for year, month in ends]
            returns = []
            for previous, current in zip(closes, closes[1:]):
                returns.append(
                    None if previous in (None, 0) or current is None
                    else round((current / previous - 1) * 100, 2)
                )
            missing = [quarter_labels[i + 1] for i, value in enumerate(returns) if value is None]
            if not any(value is not None for value in returns):
                log.warning("market_data: monthly bars did not cover requested quarter ends for %s", label)
                return None
            return {
                "label": label, "quarters": quarter_labels[1:], "returns": returns,
                "quarter_end_closes": closes[1:], "missing_quarters": missing,
                "history_source": " + ".join(history_sources) if history_sources else "no verified history provider returned data",
                "official_quarter_end_closes_recovered": official_recovered,
            }

        results = await asyncio.gather(*[
            fetch_one(symbol, label) for symbol, label in SECTOR_INDEX_SYMBOLS
        ], return_exceptions=True)
    out = [row for row in results if isinstance(row, dict)]
    log.info(
        "market_data: calculated completed-quarter returns for %d/%d sector indices (%d quarters) using month-end bars",
        len(out), len(SECTOR_INDEX_SYMBOLS), quarters,
    )
    return out


def format_sector_quarterly_returns_as_source(rows: list[dict]) -> Optional[dict]:
    """Format calculated quarterly returns as a compact auditable research source."""
    if not rows:
        return None
    quarters = rows[0].get("quarters") or []
    lines = ["COMPLETED CALENDAR-QUARTER NSE SECTOR INDEX RETURNS (calculated from observed month-end index closes; Yahoo Finance is primary and official Nifty Indices daily history is used only to recover missing quarter-end closes; this is a calculated dataset, not an official NSE publication).",
             "Method: each return = (month-end close for quarter / month-end close for prior quarter - 1) × 100. Missing quarter-end months are marked unavailable; no interpolation.",
             "Quarter coverage: " + ", ".join(quarters), "Sector | " + " | ".join(quarters)]
    for row in rows:
        values = ["N/A" if value is None else f"{value:+.2f}%" for value in row.get("returns", [])]
        lines.append(str(row.get("label") or "Unknown sector") + " | " + " | ".join(values))
    # Add one reproducible, auditable summary metric for the ranking chart. Only
    # calculate it when every quarter is observed; partial series never get
    # compounded as though missing returns were zero.
    complete_rows = []
    if len(quarters) >= 4:
        lines.append("Compounded return (calculated by multiplying each observed quarterly gross return; shown only for sectors with complete quarter coverage):")
        for row in rows:
            values = row.get("returns") or []
            if len(values) != len(quarters) or any(v is None for v in values):
                continue
            try:
                total = (math.prod(1.0 + float(v) / 100.0 for v in values) - 1.0) * 100.0
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(total):
                continue
            label = str(row.get("label") or "Unknown sector")
            complete_rows.append((label, total))
            lines.append(f"{label} compounded return across {quarters[0]} to {quarters[-1]} = {total:+.2f}%")
    provenance = [
        f"{r.get('label')}: {r.get('history_source') or 'provider not recorded'}"
        for r in rows if isinstance(r, dict)
    ]
    if provenance:
        lines.append("History source by index: " + "; ".join(provenance))
    missing = [f"{r['label']}: {', '.join(r['missing_quarters'])}" for r in rows if r.get("missing_quarters")]
    if missing:
        lines.append("Missing observations: " + "; ".join(missing))
    # The canonical source registry intentionally strips internal:// URLs and
    # only carries compact evidence fields into chart auditing. Preserve the
    # complete quarter matrix as chartEvidenceExcerpt so numeric chart points
    # remain auditable after source-manifest normalization.
    evidence_text = "\n".join(lines)
    return {
        "title": "CALCULATED NSE SECTOR RETURNS — EIGHT COMPLETED CALENDAR QUARTERS",
        "url": "internal://verified-sector-quarterly-returns",
        "snippet": " | ".join(lines[:5])[:2000],
        "fullContent": evidence_text,
        "chartEvidenceExcerpt": evidence_text[:16000],
        "evidenceOrigin": "calculated_from_daily_market_data",
        "pageFetchStatus": "ok",
        "evidenceAvailable": True,
    }


async def fetch_historical_stock_quotes(
    symbols: list[dict], range_str: str = "2y", interval: str = "3mo",
) -> list[dict]:
    """Same idea as fetch_historical_index_quotes() but for individually
    resolved company tickers, e.g. symbols=[{"symbol": "RELIANCE.NS", "name":
    "Reliance Industries"}]. Returns a real closing-price series per company
    so the report can build an actual price-trend line chart instead of
    guessing at a trajectory.
    """
    out: list[dict] = []
    async with httpx.AsyncClient() as client:
        for s in symbols[:6]:
            points = await _yf_chart_history(client, s["symbol"], range_str, interval)
            if points and len(points) >= 2:
                out.append({"label": s.get("name") or s["symbol"], "points": points})
    return out


def format_quotes_as_source(quotes: list[dict]) -> Optional[dict]:
    """Wraps fetched quotes as a synthetic, high-trust "source" dict in the
    same normalized shape as Exa results, so it can be prepended to the sources list
    that feeds the report-generation prompt."""
    if not quotes:
        return None
    lines = [
        f"{q['label']}: {q['price']:.2f} ({q['changePct']:+.2f}%)"
        for q in quotes
    ]
    return {
        "title": "VERIFIED LIVE INDEX DATA (authoritative — use these exact figures for any index price/level, do not use other sources for these numbers)",
        "url": "internal://verified-index-quotes",
        "snippet": "; ".join(lines),
        "fullContent": "\n".join(lines),
    }


async def fetch_company_ohlc(stocks: list[dict], range_str: str = "1y", interval: str = "1wk") -> list[dict]:
    """Real per-session open/high/low/close bars for resolved company tickers.

    ``stocks`` are rows from fetch_stock_fundamentals() (each with ``symbol``
    and ``name``). Returns [{"label", "symbol", "interval", "provider", "bars": [...]}]
    for every symbol Yahoo returns at least two complete sessions for. Same
    best-effort contract as the index OHLC fetcher: failures are omitted.
    """
    rows = [s for s in (stocks or []) if isinstance(s, dict) and s.get("symbol")]
    if not rows:
        return []
    out: list[dict] = []
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_yf_chart_ohlc(client, str(row["symbol"]), range_str, interval) for row in rows[:8]],
            return_exceptions=True,
        )
    for row, bars in zip(rows, results):
        if isinstance(bars, list) and len(bars) >= 2:
            out.append({
                "label": str(row.get("name") or row["symbol"]), "symbol": str(row["symbol"]),
                "interval": interval, "range": range_str, "provider": "Yahoo Finance chart API", "bars": bars,
            })
    log.info("market_data: fetched %s OHLC for %d/%d company symbol(s)", interval, len(out), len(rows))
    return out
