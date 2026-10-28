"""Make one bounded Gemini request without logging credentials or response bodies."""

import http.client
import json
import os
import sys


MODEL = "gemini-3.8-flash"
HOST = "generativelanguage.googleapis.com"
TIMEOUT_SECONDS = 30


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("FAIL: configure the GEMINI_API_KEY repository secret.", file=sys.stderr)
        return 1

    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with the word OK."}]}],
        "generationConfig": {
            "maxOutputTokens": 256,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    connection = http.client.HTTPSConnection(HOST, timeout=TIMEOUT_SECONDS)

    try:
        # A header keeps the key out of the URL. HTTPSConnection does not follow redirects.
        connection.request(
            "POST",
            f"/v1beta/models/{MODEL}:generateContent",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        )
        response = connection.getresponse()
        if response.status != 200:
            hints = {
                400: "Check the API key and request configuration.",
                401: "Check API authentication.",
                403: "Check API key restrictions, project permissions and region access.",
                404: "Check whether the configured model is available.",
                429: "Check the project's rate limits and available quota in AI Studio.",
            }
            hint = hints.get(response.status, "Check Gemini service availability.")
            print(f"FAIL: Gemini returned HTTP {response.status}. {hint}", file=sys.stderr)
            return 1

        data = json.loads(response.read())
        candidate = data["candidates"][0]
        parts = candidate["content"]["parts"]
        has_text = any(
            isinstance(part.get("text"), str)
            and part["text"].strip()
            and not part.get("thought", False)
            for part in parts
        )
        if candidate.get("finishReason") != "STOP" or not has_text:
            print("FAIL: Gemini did not return a completed text response.", file=sys.stderr)
            return 1

    except (OSError, http.client.HTTPException):
        print("FAIL: network, TLS or timeout error while contacting Gemini.", file=sys.stderr)
        return 1
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        print("FAIL: Gemini returned an unexpected response format.", file=sys.stderr)
        return 1
    finally:
        connection.close()

    print(f"PASS: {MODEL} returned a completed, non-empty text response.")
    usage = data.get("usageMetadata", {})
    if not isinstance(usage, dict):
        usage = {}
    for field in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount"):
        count = usage.get(field)
        if type(count) is int:
            print(f"{field}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
