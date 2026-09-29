"""Offline tests: no Autodesk imports and no creation of a real Revit document."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from mcp_server import bridge
from revit.revit2021.template_locator import find_template


class TemplateTests(unittest.TestCase):
    def test_prefers_unhosted_mechanical_over_generic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("Metric Mechanical Equipment.rft", "Metric Generic Model.rft",
                         "Metric Mechanical Equipment wall based.rft"):
                (root / name).touch()
            result = find_template(roots=[directory])
            self.assertEqual(Path(result["selected"]["path"]).name, "Metric Mechanical Equipment.rft")
            self.assertEqual(result["rft_files_found"], 3)
            self.assertEqual(len(result["candidates"]), 2)

    def test_fallback_and_no_suitable_template(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                find_template(roots=[directory])
            Path(directory, "Metric Generic Model.rft").touch()
            self.assertEqual(find_template(roots=[directory])["selected"]["kind"], "generic_metric")


class RoundtripGuardsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root_patch = patch.object(bridge, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.request_id = "601ba3de-6f7b-4432-94ce-68d182be6e75"
        self.endpoint = {"process_id": 123, "revit_version": "2021",
                         "url": "http://127.0.0.1:48884/mcp-health/health"}
        output = self.root / "tests" / "output" / self.request_id / "MCP_Test_Parameter_Roundtrip.rfa"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"offline fixture, not an RFA")
        self.good = {
            "request_id": self.request_id, "process_id": 123, "success": True,
            "saved": True, "closed": True, "reopened": True, "verified": True,
            "closed_after_readback": True, "transaction_committed": True, "template_unchanged": True,
            "test": "family_parameter_roundtrip", "stage": "complete", "errors": [],
            "revit_version": "2021", "revit_api_version": "21.0.0.0", "revit_apiui_version": "21.0.0.0",
            "family_type": "MCP_TEST_1000", "write_value_mm": 1000.0,
            "value_before_save_mm": 1000.0, "readback_value_mm": 1000.0, "delta_mm": 0.0,
            "output_path": str(output),
            "parameter": {"name": "MCP_Ширина", "is_instance": False,
                          "parameter_type": "Length", "parameter_group": "PG_GEOMETRY"}}

    def test_rejects_fake_success_and_bad_values(self):
        for key, value in (("verified", False), ("closed_after_readback", False),
                           ("readback_value_mm", 1001.0), ("readback_value_mm", float("nan")),
                           ("readback_value_mm", float("inf")), ("delta_mm", 1),
                           ("output_path", str(self.root / "user.rfa")), ("errors", ["failure"])):
            with self.subTest(key=key, value=value):
                data = copy.deepcopy(self.good)
                data[key] = value
                with self.assertRaises(bridge.BridgeError):
                    bridge.validate_roundtrip_result(data, self.endpoint, self.request_id)

    def test_type_and_identity_must_survive(self):
        data = copy.deepcopy(self.good)
        data["parameter"]["is_instance"] = True
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_roundtrip_result(data, self.endpoint, self.request_id)
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_roundtrip_result(self.good, {"process_id": 456}, self.request_id)

    def test_revit_error_returned_intact(self):
        data = {"request_id": self.request_id, "process_id": 123, "success": False,
                "stage": "save_family", "error": "disk full", "traceback": "original Revit trace"}
        before = json.dumps(data)
        bridge.validate_roundtrip_result(data, self.endpoint, self.request_id)
        self.assertEqual(json.dumps(data), before)

    def test_timeout_never_retries_post(self):
        live_health = {"registered_routes": [{"path": "/family-mcp/test-parameter-roundtrip", "method": "POST"}]}
        opener = Mock(open=Mock(side_effect=TimeoutError("busy")))
        with patch.object(bridge, "_select_instance", return_value=(self.endpoint, live_health)), \
                patch.object(bridge, "build_opener", return_value=opener):
            with self.assertRaisesRegex(bridge.BridgeError, "Do not retry automatically"):
                bridge.revit_test_family_parameter_roundtrip()
        self.assertEqual(opener.open.call_count, 1)

    def test_no_post_when_route_missing(self):
        with patch.object(bridge, "_select_instance", return_value=(self.endpoint, {})), \
                patch.object(bridge, "build_opener") as opener:
            with self.assertRaisesRegex(bridge.BridgeError, "not loaded"):
                bridge.revit_test_family_parameter_roundtrip()
            opener.assert_not_called()
