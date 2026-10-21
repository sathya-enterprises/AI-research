"""Bounded Playwright acquisition fallback for JS-rendered source pages."""
from __future__ import annotations

import os
import re


async def fetch_playwright_page(url: str, max_chars: int = 6000) -> str:
    if os.getenv("ENABLE_PLAYWRIGHT_FETCH", "1").strip().lower() in {"0", "false", "no", "off"}:
        return ""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 1600})
                await page.goto(url, wait_until="domcontentloaded", timeout=int(float(os.getenv("PLAYWRIGHT_FETCH_TIMEOUT", "15")) * 1000))
                await page.wait_for_timeout(750)
                text = await page.locator("body").inner_text(timeout=3000)
                text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
                return text[:max_chars]
            finally:
                await browser.close()
    except Exception:
        return ""
