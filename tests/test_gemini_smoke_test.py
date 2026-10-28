"""Offline checks for API diagnostics and protection against unsafe log output."""

import contextlib
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from scripts import gemini_smoke_test as smoke


class GeminiSmokeTest(unittest.TestCase):
    def run_failure(self, body: bytes, status: int = 503, read_error=None):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = status
        response.read.return_value = body
        response.read.side_effect = read_error
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "offline-test-secret"}, clear=True),
            patch.object(smoke.http.client, "HTTPSConnection", return_value=connection),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = smoke.main()
        self.assertEqual(result, 1)
        connection.request.assert_called_once()
        response.read.assert_called_once_with(smoke.MAX_ERROR_BYTES + 1)
        connection.close.assert_called_once()
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("offline-test-secret", stderr.getvalue())
        return stderr.getvalue()

    def test_service_unavailable_reports_status(self):
        log = self.run_failure(json.dumps({"error": {"status": "UNAVAILABLE"}}).encode())
        self.assertIn("HTTP 503", log)
        self.assertIn("Gemini status: UNAVAILABLE", log)
        self.assertIn("ErrorInfo reasons: not provided", log)

    def test_known_reason_is_reported_without_message_or_metadata(self):
        error = {
            "status": "INVALID_ARGUMENT",
            "message": "offline-test-secret ::error::unsafe-message",
            "details": [{
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "API_KEY_INVALID",
                "metadata": {"consumer": "private-project-identifier"},
            }],
        }
        log = self.run_failure(json.dumps({"error": error}).encode(), status=400)
        self.assertIn("Gemini status: INVALID_ARGUMENT", log)
        self.assertIn("ErrorInfo reasons: API_KEY_INVALID", log)
        self.assertNotIn("unsafe-message", log)
        self.assertNotIn("private-project-identifier", log)

    def test_arbitrary_status_reason_and_localized_message_cannot_enter_logs(self):
        error = {
            "status": "offline-test-secret\n::error::injected-status",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                 "reason": "offline-test-secret\n::warning::injected-reason"},
                {"@type": "type.googleapis.com/google.rpc.LocalizedMessage",
                 "message": "injected-localized-message"},
            ],
        }
        log = self.run_failure(json.dumps({"error": error}).encode())
        self.assertIn("Gemini status: UNRECOGNIZED", log)
        self.assertIn("ErrorInfo reasons: UNRECOGNIZED", log)
        self.assertNotIn("injected", log)
        self.assertNotIn("::", log)

    def test_missing_malformed_and_oversized_errors_preserve_http_failure(self):
        cases = [
            b"<html>offline-test-secret</html>",
            b"\xffoffline-test-secret",
            b"[]",
            b'{"error": null}',
            b'{"error": "offline-test-secret"}',
            b"x" * (smoke.MAX_ERROR_BYTES + 1),
        ]
        for body in cases:
            with self.subTest(body_length=len(body)):
                log = self.run_failure(body)
                self.assertIn("HTTP 503", log)
                self.assertIn("Diagnostics unavailable:", log)

    def test_unexpected_field_types_cannot_crash_diagnostics(self):
        cases = [
            {"status": [], "details": "offline-test-secret"},
            {"status": {}, "details": [None, [], "offline-test-secret"]},
            {"status": "UNAVAILABLE", "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": []},
            ]},
        ]
        for error in cases:
            with self.subTest(error=error):
                self.assertIn("Gemini status:", self.run_failure(json.dumps({"error": error}).encode()))

    def test_body_read_failure_does_not_hide_http_status(self):
        log = self.run_failure(b"", read_error=TimeoutError("offline-test-secret"))
        self.assertIn("HTTP 503", log)
        self.assertIn("could not read the error body", log)

    def test_success_still_reports_token_usage_without_response_text(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "OK"}]}}],
            "usageMetadata": {"totalTokenCount": 8},
        }).encode()
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "offline-test-secret"}, clear=True),
            patch.object(smoke.http.client, "HTTPSConnection", return_value=connection),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(smoke.main(), 0)
        self.assertIn("PASS:", output.getvalue())
        self.assertIn("totalTokenCount: 8", output.getvalue())
        self.assertNotIn("offline-test-secret", output.getvalue())
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
