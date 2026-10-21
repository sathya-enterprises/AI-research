import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# MODELS TO TEST
# ============================================================

MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]

# ============================================================
# LOAD GEMINI KEYS
# ============================================================

def load_keys():
    raw = os.getenv("GEMINI_API_KEY", "")

    if not raw:
        return []

    # Support:
    # comma-separated
    # semicolon-separated
    # newline-separated
    raw = raw.replace(";", ",").replace("\n", ",")

    keys = []

    for key in raw.split(","):
        key = key.strip()

        if key and key not in keys:
            keys.append(key)

    return keys


# ============================================================
# MASK KEY
# ============================================================

def masked(key):
    if len(key) <= 8:
        return "..." + key[-4:]

    return key[:4] + "..." + key[-4:]


# ============================================================
# TEST ONE KEY + ONE MODEL
# ============================================================

async def check_key_model(client, key, model):
    """Manual live API probe used by this module's explicit command entrypoint.

    This helper requires caller-supplied client/key/model values and is not a
    pytest test fixture. Keeping the `test_` prefix made normal test discovery
    fail with a missing-client fixture before the opt-in diagnostic ran.
    """

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
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

        # ----------------------------------------------------
        # WORKING
        # ----------------------------------------------------

        if status == 200:

            try:
                data = response.json()

                text = (
                    data["candidates"][0]
                    ["content"]["parts"][0]
                    ["text"]
                ).strip()

                if text:
                    return {
                        "status": "WORKING",
                        "http": 200,
                        "message": text
                    }

            except Exception:
                pass

            return {
                "status": "WORKING",
                "http": 200,
                "message": "Response received"
            }

        # ----------------------------------------------------
        # INVALID REQUEST
        # ----------------------------------------------------

        if status == 400:
            return {
                "status": "BAD REQUEST",
                "http": 400,
                "message": "Invalid request / model configuration"
            }

        # ----------------------------------------------------
        # AUTHENTICATION
        # ----------------------------------------------------

        if status == 401:
            return {
                "status": "AUTH ERROR",
                "http": 401,
                "message": "Authentication failed"
            }

        # ----------------------------------------------------
        # PERMISSION
        # ----------------------------------------------------

        if status == 403:
            return {
                "status": "FORBIDDEN",
                "http": 403,
                "message": "API/model permission denied"
            }

        # ----------------------------------------------------
        # MODEL DOES NOT EXIST / AVAILABLE
        # ----------------------------------------------------

        if status == 404:
            return {
                "status": "MODEL UNAVAILABLE",
                "http": 404,
                "message": "Model unavailable"
            }

        # ----------------------------------------------------
        # RATE LIMIT
        # ----------------------------------------------------

        if status == 429:
            return {
                "status": "RATE LIMITED",
                "http": 429,
                "message": "Quota/rate limit"
            }

        # ----------------------------------------------------
        # SERVER ERRORS
        # ----------------------------------------------------

        if status == 500:
            return {
                "status": "SERVER ERROR",
                "http": 500,
                "message": "Gemini server error"
            }

        if status == 502:
            return {
                "status": "SERVER ERROR",
                "http": 502,
                "message": "Bad gateway"
            }

        if status == 503:
            return {
                "status": "OVERLOADED",
                "http": 503,
                "message": "Gemini overloaded/unavailable"
            }

        return {
            "status": "OTHER",
            "http": status,
            "message": response.text[:150]
        }

    except httpx.TimeoutException:

        return {
            "status": "TIMEOUT",
            "http": "-",
            "message": "Request timed out"
        }

    except Exception as e:

        return {
            "status": "ERROR",
            "http": "-",
            "message": f"{type(e).__name__}: {e}"
        }


# ============================================================
# MAIN
# ============================================================

async def main():

    keys = load_keys()

    print()
    print("=" * 100)
    print("GEMINI KEY × MODEL TEST")
    print("=" * 100)

    print()
    print("Models being tested:")

    for model in MODELS:
        print(f"  - {model}")

    print()
    print(f"Gemini keys found: {len(keys)}")
    print()

    if not keys:

        print("ERROR: No Gemini API keys found.")
        print()
        print("Check GEMINI_API_KEY in your .env file.")
        return

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    results = {}

    working_combinations = []
    failed_combinations = []

    model_stats = {
        model: {
            "working": 0,
            "bad_request": 0,
            "auth_error": 0,
            "forbidden": 0,
            "unavailable": 0,
            "rate_limited": 0,
            "overloaded": 0,
            "timeout": 0,
            "other": 0,
        }
        for model in MODELS
    }

    timeout = httpx.Timeout(
        connect=10,
        read=35,
        write=10,
        pool=10
    )

    async with httpx.AsyncClient(timeout=timeout) as client:

        # ====================================================
        # TEST EVERY KEY AGAINST EVERY MODEL
        # ====================================================

        for key_index, key in enumerate(keys, 1):

            key_display = masked(key)

            print()
            print("-" * 100)
            print(
                f"KEY [{key_index}/{len(keys)}] "
                f"{key_display}"
            )
            print("-" * 100)

            results[key_display] = {}

            for model in MODELS:

                print(
                    f"  {model:<28}",
                    end="",
                    flush=True
                )

                result = await check_key_model(
                    client,
                    key,
                    model
                )

                status = result["status"]
                http_status = result["http"]
                message = result["message"]

                results[key_display][model] = result

                print(
                    f"{status:<18} "
                    f"HTTP {http_status}"
                )

                # ------------------------------------------------
                # Statistics
                # ------------------------------------------------

                stats = model_stats[model]

                if status == "WORKING":
                    stats["working"] += 1

                    working_combinations.append(
                        (key_display, model)
                    )

                elif status == "BAD REQUEST":
                    stats["bad_request"] += 1
                    failed_combinations.append(
                        (key_display, model, status)
                    )

                elif status == "AUTH ERROR":
                    stats["auth_error"] += 1
                    failed_combinations.append(
                        (key_display, model, status)
                    )

                elif status == "FORBIDDEN":
                    stats["forbidden"] += 1
                    failed_combinations.append(
                        (key_display, model, status)
                    )

                elif status == "MODEL UNAVAILABLE":
                    stats["unavailable"] += 1
                    failed_combinations.append(
                        (key_display, model, status)
                    )

                elif status == "RATE LIMITED":
                    stats["rate_limited"] += 1
                    failed_combinations.append(
                        (key_display, model, status)
                    )

                elif status == "OVERLOADED":
                    stats["overloaded"] += 1
                    failed_combinations.append(
                        (key_display, model, status)
                    )

                elif status == "TIMEOUT":
                    stats["timeout"] += 1
                    failed_combinations.append(
                        (key_display, model, status)
                    )

                else:
                    stats["other"] += 1
                    failed_combinations.append(
                        (key_display, model, status)
                    )

                # Small pause between requests
                await asyncio.sleep(0.3)

    # ========================================================
    # MODEL SUMMARY
    # ========================================================

    print()
    print()
    print("=" * 100)
    print("MODEL SUMMARY")
    print("=" * 100)

    print()

    print(
        f"{'MODEL':<30}"
        f"{'WORKING':<12}"
        f"{'400':<8}"
        f"{'401':<8}"
        f"{'403':<8}"
        f"{'404':<8}"
        f"{'429':<8}"
        f"{'503':<8}"
        f"{'TIMEOUT':<10}"
    )

    print("-" * 100)

    for model in MODELS:

        s = model_stats[model]

        print(
            f"{model:<30}"
            f"{s['working']:<12}"
            f"{s['bad_request']:<8}"
            f"{s['auth_error']:<8}"
            f"{s['forbidden']:<8}"
            f"{s['unavailable']:<8}"
            f"{s['rate_limited']:<8}"
            f"{s['overloaded']:<8}"
            f"{s['timeout']:<10}"
        )

    # ========================================================
    # WORKING COMBINATIONS
    # ========================================================

    print()
    print()
    print("=" * 100)
    print("WORKING KEY + MODEL COMBINATIONS")
    print("=" * 100)

    if working_combinations:

        for key_display, model in working_combinations:

            print(
                f"{key_display:<18}  {model}"
            )

    else:

        print("NONE")

    # ========================================================
    # FAILED COMBINATIONS
    # ========================================================

    print()
    print()
    print("=" * 100)
    print("FAILED KEY + MODEL COMBINATIONS")
    print("=" * 100)

    if failed_combinations:

        for key_display, model, status in failed_combinations:

            print(
                f"{key_display:<18}  "
                f"{model:<30}  "
                f"{status}"
            )

    else:

        print("NONE")

    # ========================================================
    # BEST MODELS
    # ========================================================

    print()
    print()
    print("=" * 100)
    print("BEST MODELS")
    print("=" * 100)

    ranked_models = sorted(
        MODELS,
        key=lambda model: model_stats[model]["working"],
        reverse=True
    )

    for position, model in enumerate(
        ranked_models,
        1
    ):

        working_count = model_stats[model]["working"]

        print(
            f"{position}. "
            f"{model:<30} "
            f"{working_count}/{len(keys)} keys working"
        )

    # ========================================================
    # FINAL TOTALS
    # ========================================================

    total_tests = len(keys) * len(MODELS)
    total_working = len(working_combinations)

    print()
    print()
    print("=" * 100)
    print("FINAL TOTAL")
    print("=" * 100)

    print()
    print(f"Keys tested:        {len(keys)}")
    print(f"Models tested:      {len(MODELS)}")
    print(f"Total tests:        {total_tests}")
    print(f"Working tests:      {total_working}")
    print(f"Failed tests:       {total_tests - total_working}")

    print()
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(main())
