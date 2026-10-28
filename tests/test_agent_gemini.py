"""Offline checks for the planner's HTTP adapter, including safe failure output."""

import json
import unittest
from unittest.mock import MagicMock, patch

from test_agent.context import AgentError
from test_agent.gemini import MAX_RESPONSE_BYTES, GeminiClient


class GeminiClientTests(unittest.TestCase):
    def test_request_uses_header_and_passes_tools_without_builtin_execution(self):
        content = {
            "role": "model",
            "parts": [{"functionCall": {"name": "read_module", "args": {}}}],
        }
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(
            {
                "candidates": [{"finishReason": "STOP", "content": content}],
                "usageMetadata": {"totalTokenCount": 8},
            }
        ).encode()
        client = GeminiClient("offline-key")
        with patch(
            "test_agent.gemini.http.client.HTTPSConnection", return_value=connection
        ):
            self.assertEqual(
                client.generate("trusted", [], [{"name": "read_module"}]), content
            )
        args, kwargs = connection.request.call_args
        self.assertNotIn("offline-key", args[1])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "offline-key")
        payload = json.loads(kwargs["body"])
        self.assertEqual(
            payload["tools"], [{"functionDeclarations": [{"name": "read_module"}]}]
        )
        self.assertEqual(payload["toolConfig"]["functionCallingConfig"]["mode"], "ANY")
        response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
        connection.close.assert_called_once()
        self.assertEqual(client.usage, {"totalTokenCount": 8})

    def test_remote_error_text_and_network_exception_do_not_leak_the_key(self):
        for status, error in [(503, None), (200, OSError("offline-key"))]:
            connection = MagicMock()
            response = connection.getresponse.return_value
            response.status = status
            response.read.return_value = (
                b'{"error":{"status":"UNAVAILABLE","message":"offline-key"}}'
            )
            connection.request.side_effect = error
            with (
                self.subTest(status=status),
                patch(
                    "test_agent.gemini.http.client.HTTPSConnection",
                    return_value=connection,
                ),
                self.assertRaises(AgentError) as raised,
            ):
                GeminiClient("offline-key").generate("trusted", [], [])
            self.assertNotIn("offline-key", str(raised.exception))
            connection.request.assert_called_once()
            connection.close.assert_called_once()

    def test_oversized_or_truncated_responses_cannot_produce_a_plan(self):
        for body in [
            b"x" * (MAX_RESPONSE_BYTES + 1),
            b'{"candidates":[{"finishReason":"MAX_TOKENS"}]}',
        ]:
            connection = MagicMock()
            response = connection.getresponse.return_value
            response.status = 200
            response.read.return_value = body
            with (
                self.subTest(body_length=len(body)),
                patch(
                    "test_agent.gemini.http.client.HTTPSConnection",
                    return_value=connection,
                ),
                self.assertRaises(AgentError),
            ):
                GeminiClient("offline-key").generate("trusted", [], [])

    def test_failed_error_body_read_preserves_the_http_status(self):
        connection = MagicMock()
        connection.getresponse.return_value.status = 503
        connection.getresponse.return_value.read.side_effect = TimeoutError(
            "offline-key"
        )
        with patch(
            "test_agent.gemini.http.client.HTTPSConnection", return_value=connection
        ):
            with self.assertRaisesRegex(AgentError, "Gemini HTTP 503") as raised:
                GeminiClient("offline-key").generate("trusted", [], [])
        self.assertNotIn("offline-key", str(raised.exception))
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
