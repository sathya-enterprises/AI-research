import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

MODEL = "gemini-3.6-flash"

def load_keys():
    raw = os.getenv("GEMINI_API_KEY", "")

    if not raw:
        return []

    # Support comma, semicolon and newline separated keys
    raw = raw.replace(";", ",").replace("\n", ",")

    keys = []
    for key in raw.split(","):
        key = key.strip()
        if key and key not in keys:
            keys.append(key)

    return keys


def masked(key):
    if len(key) <= 8:
        return "..." + key[-4:]
    return key[:4] + "..." + key[-4:]


async def test_key(client, key):
    url = (
        f"https://generativelanguage.googleapis.com/"
        f"v1beta/models/{MODEL}:generateContent"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": "Reply with exactly: OK"
                    }
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 10
        }
    }

    try:
        response = await client.post(
            url,
            params={"key": key},
            json=payload
        )

        status = response.status_code

        if status == 200:
            try:
                data = response.json()
                text = (
                    data["candidates"][0]["content"]["parts"][0]["text"]
                ).strip()

                if text:
                    return "WORKING", status, text

            except Exception:
                pass

            return "WORKING", status, "Response received"

        if status == 400:
            return "NOT WORKING", status, "Bad request / invalid configuration"

        if status == 401:
            return "NOT WORKING", status, "Authentication failed"

        if status == 403:
            return "NOT WORKING", status, "Permission / API access denied"

        if status == 404:
            return "MODEL ERROR", status, "Model not available"

        if status == 429:
            return "RATE LIMITED", status, "Quota / rate limit"

        if status == 500:
            return "SERVER ERROR", status, "Gemini server error"

        if status == 502:
            return "SERVER ERROR", status, "Bad gateway"

        if status == 503:
            return "SERVER ERROR", status, "Gemini overloaded/unavailable"

        return "OTHER", status, response.text[:200]

    except httpx.TimeoutException:
        return "TIMEOUT", "-", "Request timed out"

    except Exception as e:
        return "ERROR", "-", f"{type(e).__name__}: {e}"


async def main():

    keys = load_keys()

    print("=" * 80)
    print("GEMINI API KEY TEST")
    print("=" * 80)
    print(f"Model: {MODEL}")
    print(f"Keys found: {len(keys)}")
    print()

    if not keys:
        print("NO GEMINI KEYS FOUND")
        print()
        print("Check GEMINI_API_KEY in your .env file.")
        return

    working = []
    not_working = []
    rate_limited = []
    server_errors = []
    other = []

    timeout = httpx.Timeout(
        connect=10,
        read=30,
        write=10,
        pool=10
    )

    async with httpx.AsyncClient(timeout=timeout) as client:

        for i, key in enumerate(keys, 1):

            print(
                f"[{i:02d}/{len(keys):02d}] "
                f"{masked(key):<18} ",
                end="",
                flush=True
            )

            result, status, message = await test_key(client, key)

            print(
                f"{result:<15} "
                f"HTTP {status}  "
                f"{message}"
            )

            item = masked(key)

            if result == "WORKING":
                working.append(item)

            elif result == "NOT WORKING":
                not_working.append(item)

            elif result == "RATE LIMITED":
                rate_limited.append(item)

            elif result == "SERVER ERROR":
                server_errors.append(item)

            else:
                other.append(item)

            # Small delay so we don't hammer the API
            await asyncio.sleep(0.3)

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    print()
    print("WORKING:")
    print(", ".join(working) if working else "NONE")

    print()
    print("NOT WORKING:")
    print(", ".join(not_working) if not_working else "NONE")

    print()
    print("RATE LIMITED:")
    print(", ".join(rate_limited) if rate_limited else "NONE")

    print()
    print("SERVER ERRORS:")
    print(", ".join(server_errors) if server_errors else "NONE")

    print()
    print("OTHER:")
    print(", ".join(other) if other else "NONE")

    print()
    print("=" * 80)
    print(
        f"TOTAL: {len(keys)} | "
        f"WORKING: {len(working)} | "
        f"NOT WORKING: {len(not_working)} | "
        f"RATE LIMITED: {len(rate_limited)} | "
        f"SERVER ERRORS: {len(server_errors)}"
    )
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())