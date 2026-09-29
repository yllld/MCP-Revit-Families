import copy
import json
from pathlib import Path
import unittest
from core.detail_plan import detail_plan, geometry, profile, compare_bounds


class DetailPlanTests(unittest.TestCase):
    def setUp(self):
        self.before = {"ready": True, "is_read_only": False, "context_token": "test-token",
                       "parameter_count": 0, "existing_geometry": {"elements": []}}
        self.spec = json.loads(Path("examples/details.dry-run.json").read_text(encoding="utf-8"))["spec"]

    def plan(self):
        return detail_plan(self.before, self.spec, [])

    def test_any_equipment_without_baseline_parameters_or_receivers(self):
        result = self.plan()
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(result["detail_count"], 4)
        self.assertTrue(result["visual_review_required"])
        self.assertFalse(result["parametric_details"])

    def test_legacy_profiles_and_silent_unknown_fields_rejected(self):
        for value in ({"profile": "wos8_drawing_3400401"}, {"receiver_refinement": {}}, {"connectors": []}, {"family_parameters": []}):
            spec = dict(self.spec, **value)
            self.assertFalse(detail_plan(self.before, spec, [])["ready"])

    def test_malformed_json_values_return_plan_errors(self):
        for value in (None, [], "text", 1, {"schema_version": 1}):
            self.assertFalse(detail_plan(self.before, value, [])["ready"])
        for value in (None, [], "text", 1, {"logical_id": []}):
            spec = dict(self.spec, details=[value])
            self.assertFalse(detail_plan(self.before, spec, [])["ready"])

    def test_nan_infinite_boolean_and_negative_dimensions_rejected(self):
        item = self.spec["details"][1]["geometry"]
        for value in (float("nan"), float("inf"), True, -1, 0):
            item["depth_mm"] = value
            self.assertFalse(self.plan()["ready"])

    def test_orthonormal_frame_and_unsupported_geometry(self):
        item = self.spec["details"][1]["geometry"]
        item["frame"]["v"] = [1, 0, 0]
        self.assertFalse(self.plan()["ready"])
        item["kind"] = "execute_python"
        self.assertFalse(self.plan()["ready"])

    def test_unknown_nested_fields_rejected(self):
        self.spec["details"][0]["geometry"]["left"]["loops"][0]["fillet"] = 3
        self.assertFalse(self.plan()["ready"])

    def test_duplicate_ids_and_repeat_rejected(self):
        self.spec["details"][1]["logical_id"] = self.spec["details"][0]["logical_id"]
        self.assertFalse(self.plan()["ready"])
        self.setUp()
        existing = [{"logical_id": self.spec["details"][0]["logical_id"]}]
        self.assertFalse(detail_plan(self.before, self.spec, existing)["ready"])

    def test_only_explicit_existing_generated_forms_replaceable(self):
        record = {"logical_id": "equipment/base", "unique_id": "owned-1", "element_id": 123}
        self.spec["replace_generated_ids"] = [record["logical_id"]]
        self.assertFalse(detail_plan(self.before, self.spec, [record])["ready"])
        self.before["existing_geometry"]["elements"] = [{"unique_id": "owned-1"}]
        result = detail_plan(self.before, self.spec, [record])
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(result["replacement_elements"], [record])
        self.assertFalse(detail_plan(self.before, self.spec, [record, record])["ready"])

    def test_requirements_cannot_omit_or_invent_detail(self):
        self.spec["requirements"] = self.spec["requirements"][:1]
        self.assertFalse(self.plan()["ready"])
        self.spec["requirements"][0]["detail_ids"].append("missing")
        self.assertFalse(self.plan()["ready"])

    def test_source_and_local_hash_required(self):
        item = self.spec["details"][0]
        item["source"] = {}
        self.assertFalse(self.plan()["ready"])
        item["source"] = {"source_type": "direct", "field": "drawing", "file": "drawing.pdf"}
        self.assertFalse(self.plan()["ready"])
        self.spec["source_documents"] = [{"file": "drawing.pdf", "sha256": "a"*64}]
        self.assertTrue(self.plan()["ready"], self.plan()["errors"])

    def test_scaled_geometry_needs_acceptance_and_valid_calibration(self):
        item = self.spec["details"][0]
        item["accuracy"] = "scaled"
        item["source"].update(source_type="derived", calculation="100 mm / 200 px * 40 px = 20 mm",
                              page=1, view="front", calibration={"known_mm": 100, "known_drawing_units": 200,
                              "measured_drawing_units": 40, "result_mm": 20, "uncertainty_mm": 1})
        self.assertFalse(self.plan()["ready"])
        self.spec["accept_scaled_dimensions"] = True
        self.assertTrue(self.plan()["ready"], self.plan()["errors"])
        item["source"]["calibration"]["result_mm"] = 25
        self.assertFalse(self.plan()["ready"])

    def test_assumptions_cannot_pass_as_confirmed_geometry(self):
        item = self.spec["details"][0]
        item["accuracy"] = "assumed"
        self.assertFalse(self.plan()["ready"])
        self.spec["accept_assumptions"] = True
        item["source"]["reason"] = "Hidden face not shown; user accepted a flat back"
        self.assertTrue(self.plan()["ready"], self.plan()["errors"])
        self.assertTrue(any("assumed geometry" in s for s in self.plan()["warnings"]))

    def test_complexity_budget_prevents_unbounded_csg(self):
        shape = self.spec["details"][1]["geometry"]
        for _ in range(8):
            shape = {"kind": "boolean", "operation": "union", "left": shape,
                     "right": copy.deepcopy(self.spec["details"][1]["geometry"])}
        with self.assertRaisesRegex(ValueError, "complexity"):
            geometry(shape, [0])

    def test_loft_must_have_ordered_sections(self):
        shape = self.spec["details"][3]["geometry"]
        shape["sections"][1]["offset_mm"] = 0
        with self.assertRaises(ValueError):
            geometry(shape, [0])

    def test_arcs_must_be_closed_and_nondegenerate(self):
        p = {"kind": "curves", "segments": [
            {"kind": "arc", "start_mm": [-10, 0], "end_mm": [10, 0], "mid_mm": [0, 10]},
            {"kind": "line", "start_mm": [10, 0], "end_mm": [-10, 0]}]}
        profile(p)
        p["segments"][0]["mid_mm"] = [0, 0]
        with self.assertRaisesRegex(ValueError, "collinear"):
            profile(p)
        p["segments"][0]["mid_mm"] = [0, 10]
        p["segments"][1]["end_mm"] = [-9, 0]
        with self.assertRaisesRegex(ValueError, "closed"):
            profile(p)

    def test_bounds_check_detects_translation_and_undersized_assembly(self):
        expected = {"min_mm": [0, 0, 0], "max_mm": [100, 100, 100]}
        for actual in ({"min_mm": [1, 0, 0], "max_mm": [101, 100, 100]},
                       {"min_mm": [0, 0, 0], "max_mm": [90, 100, 100]}):
            self.assertFalse(compare_bounds(actual, expected, .1)["success"])
        self.assertTrue(compare_bounds(expected, expected, .1)["success"])
