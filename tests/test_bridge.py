"""Offline bridge guards. These tests do not claim Revit integration success."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from mcp_server.bridge import BridgeError, NoRedirects, get_health, revit_health_check, validate_endpoint


def endpoint():
    return {"revit_version": "2021", "process_id": 123,
            "url": "http://127.0.0.1:48884/mcp-health/health"}


def health():
    return {"success": True, "revit_version": "2021", "revit_api_version": "21.0.0.0",
            "revit_apiui_version": "21.0.0.0", "routes_available": True,
            "process_id": 123, "pyrevit_version": "5.1.0.25094+1131-wip",
            "revit_build": "21.1.100.12", "checked_at_utc": "test-fixture",
            "document_title": None, "is_family_document": False}


class BridgeTests(unittest.TestCase):
    def call_with_response(self, data):
        response = io.BytesIO(json.dumps(data).encode("utf-8"))
        response.geturl = lambda: endpoint()["url"]
        with patch("mcp_server.bridge.build_opener", return_value=Mock(open=Mock(return_value=response))):
            return get_health(endpoint())

    def test_zero_document_response(self):
        self.assertIsNone(self.call_with_response(health())["document_title"])

    def test_rejects_wrong_revit_and_process_and_api(self):
        for key, value in (("revit_version", "2025"), ("process_id", 456),
                           ("revit_api_version", "25.0.0.0"),
                           ("revit_apiui_version", "25.0.0.0"),
                           ("pyrevit_version", "6.0"), ("success", False)):
            with self.subTest(key=key):
                data = health()
                data[key] = value
                with self.assertRaises(BridgeError):
                    self.call_with_response(data)

    def test_rejects_non_loopback_and_wrong_routes(self):
        for url in ("http://example.com/mcp-health/health", "file:///tmp/data",
                    "http://127.0.0.1:48884/other", "http://user:pass@127.0.0.1:48884/mcp-health/health",
                    "http://127.0.0.1:48884/mcp-health/health?callback=https://example.com"):
            with self.subTest(url=url):
                data = endpoint()
                data["url"] = url
                with self.assertRaises(BridgeError):
                    validate_endpoint(data)

    def test_rejects_malformed_endpoint(self):
        for data in ([], None, {}, {"revit_version": "2025"}):
            with self.assertRaises(BridgeError):
                validate_endpoint(data)

    def test_redirect_blocked_before_following(self):
        with self.assertRaises(BridgeError):
            NoRedirects().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_missing_and_multiple_instances(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(BridgeError, "No Revit 2021"):
                revit_health_check(directory)
            for pid in (123, 456):
                data = endpoint()
                data["process_id"] = pid
                Path(directory, "revit2021-{0}.json".format(pid)).write_text(json.dumps(data))
            with patch("mcp_server.bridge.get_health", return_value=health()):
                with self.assertRaisesRegex(BridgeError, "Multiple Revit 2021"):
                    revit_health_check(directory)
                with patch.dict("os.environ", {"REVIT_PROCESS_ID": "123"}):
                    self.assertTrue(revit_health_check(directory)["success"])

    def test_dead_endpoint_returns_failure(self):
        with patch("mcp_server.bridge.build_opener", return_value=Mock(open=Mock(side_effect=TimeoutError()))):
            with self.assertRaisesRegex(BridgeError, "Routes unavailable"):
                get_health(endpoint(), timeout=0.1)


if __name__ == "__main__":
    unittest.main()
