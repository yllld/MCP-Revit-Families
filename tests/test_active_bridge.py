"""Active-family transport guards; these do not modify or simulate a Revit model."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from mcp_server import bridge


class ActiveBridgeTests(unittest.TestCase):
    endpoint = {"process_id": 123, "url": "http://127.0.0.1:48884/mcp-health/health"}
    health = {"has_active_document": True, "is_family_document": True}

    def test_project_or_missing_document_never_posts(self):
        for health in ({}, {"has_active_document": True, "is_family_document": False}):
            with self.subTest(health=health), patch.object(bridge, "_select_instance", return_value=(self.endpoint, health)), \
                    patch.object(bridge, "build_opener") as opener:
                with self.assertRaisesRegex(bridge.BridgeError, "Open the intended RFA"):
                    bridge.active_family_call("test-geometry", {"dry_run": False})
                opener.assert_not_called()

    def test_write_timeout_never_retries(self):
        opener = Mock(open=Mock(side_effect=TimeoutError("busy")))
        with patch.object(bridge, "_select_instance", return_value=(self.endpoint, self.health)), \
                patch.object(bridge, "build_opener", return_value=opener):
            with self.assertRaisesRegex(bridge.BridgeError, "outcome may be unknown"):
                bridge.active_family_call("test-geometry", {"dry_run": False})
        self.assertEqual(opener.open.call_count, 1)

    def test_identity_checked_and_rollback_report_preserved(self):
        for process_id in (123, 456):
            data = {"process_id": process_id, "request_id": "test-id", "success": False,
                    "transaction_group_rolled_back": True, "error": "Revit rejected dimension"}
            response = io.BytesIO(json.dumps(data).encode())
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(bridge, "ROOT", Path(directory)), \
                    patch.object(bridge, "_select_instance", return_value=(self.endpoint, self.health)), \
                    patch.object(bridge.uuid, "uuid4", return_value="test-id"), \
                    patch.object(bridge, "build_opener", return_value=Mock(open=Mock(return_value=response))):
                if process_id != 123:
                    with self.assertRaisesRegex(bridge.BridgeError, "identity mismatch"):
                        bridge.active_family_call("test-geometry", {"dry_run": False})
                else:
                    actual = bridge.active_family_call("test-geometry", {"dry_run": False})
                    self.assertEqual(actual, data)
                    saved = Path(directory, "reports/codex_active_family_geometry_test.json")
                    self.assertEqual(json.loads(saved.read_text()), data)
