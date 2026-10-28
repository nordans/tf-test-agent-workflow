"""Small REST adapter: bounded requests, safe errors, no shell or file tools."""

import http.client
import json

from scripts.gemini_smoke_test import MAX_ERROR_BYTES, error_diagnostics
from test_agent.context import AgentError

MODEL = "gemini-3.5-flash-lite"
MAX_RESPONSE_BYTES = 262_144


class GeminiClient:
    def __init__(self, api_key: str):
        if not api_key.strip():
            raise AgentError("Set GEMINI_API_KEY before running the planner.")
        self._api_key = api_key.strip()
        self.usage = {}

    def generate(self, instructions: str, contents: list, tools: list) -> dict:
        payload = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": contents,
            "tools": [{"functionDeclarations": tools}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
            "generationConfig": {
                "maxOutputTokens": 4096,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        }
        connection = http.client.HTTPSConnection(
            "generativelanguage.googleapis.com", timeout=60
        )
        try:
            connection.request(
                "POST",
                f"/v1beta/models/{MODEL}:generateContent",
                body=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                try:
                    diagnostic = error_diagnostics(response.read(MAX_ERROR_BYTES + 1))
                except (OSError, http.client.HTTPException):
                    diagnostic = (
                        "Diagnostics unavailable: could not read the error body."
                    )
                raise AgentError(f"Gemini HTTP {response.status}. {diagnostic}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise AgentError("Gemini response exceeds the size limit.")
            data = json.loads(body)
            candidate = data["candidates"][0]
            content = candidate["content"]
            if candidate.get("finishReason") != "STOP" or not isinstance(
                content.get("parts"), list
            ):
                raise AgentError(
                    "Gemini did not return a completed tool-calling response."
                )
            for name, count in data.get("usageMetadata", {}).items():
                if (
                    name
                    in (
                        "promptTokenCount",
                        "candidatesTokenCount",
                        "thoughtsTokenCount",
                        "totalTokenCount",
                    )
                    and type(count) is int
                ):
                    self.usage[name] = self.usage.get(name, 0) + count
            return content
        except (OSError, http.client.HTTPException):
            raise AgentError(
                "Network, TLS or timeout error while contacting Gemini."
            ) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AgentError("Gemini returned an unexpected response format.") from None
        finally:
            connection.close()
