"""Probe how Gemini behaves at report-sized loads (tiny vs large prompt/output, streamed vs not).

Usage (from backend/):  python scripts/probe_gemini_load.py [key_index] [model]
Reads GEMINI_API_KEY from .env; keys are never printed.
"""
import asyncio
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
FILLER = ("India foreign portfolio investor flows, NSDL monthly data, SEBI bulletin tables, "
          "Nifty ownership, DII SIP inflows, primary market IPO QIP issuance. ") * 400  # ~28k chars

CASES = [
    ("tiny",           "Reply with exactly: OK", 10),
    ("26k in / 4k out", FILLER[:26_000] + "\n\nWrite a 3-section analytical summary of the above.", 4_096),
    ("26k in / 16k out", FILLER[:26_000] + "\n\nWrite a long, detailed multi-section report on the above.", 16_384),
    ("26k in / 65k cap", FILLER[:26_000] + "\n\nWrite a long, detailed multi-section report on the above.", 65_536),
]


def body(prompt: str, max_out: int) -> dict:
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_out, "thinkingConfig": {"thinkingLevel": "minimal"}},
    }


async def run_once(client, key, model, label, prompt, max_out, stream):
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    t0 = time.perf_counter()
    first = None
    chars = 0
    try:
        if stream:
            url = f"{BASE}/{model}:streamGenerateContent?alt=sse"
            async with client.stream("POST", url, headers=headers, json=body(prompt, max_out)) as r:
                if r.status_code != 200:
                    await r.aread()
                    return f"HTTP {r.status_code} after {time.perf_counter() - t0:.1f}s"
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        if first is None:
                            first = time.perf_counter() - t0
                        chars += len(line)
        else:
            r = await client.post(f"{BASE}/{model}:generateContent", headers=headers, json=body(prompt, max_out))
            if r.status_code != 200:
                return f"HTTP {r.status_code} after {time.perf_counter() - t0:.1f}s"
            chars = len(r.text)
        total = time.perf_counter() - t0
        ttfb = f"first chunk {first:.1f}s, " if first is not None else ""
        return f"200 OK {ttfb}total {total:.1f}s, ~{chars} chars"
    except httpx.TimeoutException as exc:
        return f"{type(exc).__name__} after {time.perf_counter() - t0:.1f}s"
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        return f"{type(exc).__name__}: {exc}"


async def main():
    raw = os.getenv("GEMINI_API_KEY", "").replace(";", ",").replace("\n", ",")
    keys = [k.strip().strip('"').strip("'") for k in raw.split(",") if k.strip()]
    if not keys:
        print("No GEMINI_API_KEY found in .env")
        return
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    model = sys.argv[2] if len(sys.argv) > 2 else "gemini-3.6-flash"
    key = keys[idx % len(keys)]
    print(f"model={model} key #{idx % len(keys)} of {len(keys)}  (read timeout 180s)\n")
    timeout = httpx.Timeout(connect=10, read=180, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for label, prompt, max_out in CASES:
            for stream in (False, True):
                mode = "stream " if stream else "regular"
                result = await run_once(client, key, model, label, prompt, max_out, stream)
                print(f"{label:<18} {mode}  {result}", flush=True)
                await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
