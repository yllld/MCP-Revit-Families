import json
from pathlib import Path
import unittest
from core.builder_plan import production_plan
from core.geometry_parameters import evaluate_formula, dependencies


class AssemblyPlanTests(unittest.TestCase):
    def test_length_literal_keeps_dimension_and_dependency(self):
        self.assertEqual(dependencies("H - 645 mm"), {"H"})
        self.assertEqual(evaluate_formula("H - 645 mm", lambda n: (1740, (1, 0))), (1095, (1, 0)))

    def test_airhorse_dimensions_cylinders_and_metadata(self):
        spec = json.loads(Path("tests/fixtures/airhorse_geometry.json").read_text(encoding="utf-8"))
        before = {"current_type": "base", "types": ["base"], "parameters": [], "existing_geometry": {"has_existing_geometry": False}}
        plan = production_plan(before, "build", spec)
        self.assertTrue(plan["ready"], plan["errors"])
        self.assertEqual(len(plan["geometry_to_create"]), 7)
        tanks = [b for b in plan["geometry_to_create"] if b["kind"] == "cylinder"]
        self.assertEqual(len(tanks), 2)
        for tank in tanks:
            self.assertEqual(tank["dimensions_mm"], [1950, 460, 460])
        self.assertEqual(plan["expected_geometry_parameters_by_type"]["BPM-40A"]["h1"], 1095)
        self.assertEqual(plan["adsk_parameters_to_write"], [])
        self.assertEqual(plan["overall_dimensions_mm"], [2250, 1200, 1740])
        self.assertEqual(len(plan["source_documents"]), 2)

    def test_asymmetric_cylinder_and_untraced_position_rejected(self):
        spec = json.loads(Path("tests/fixtures/airhorse_geometry.json").read_text(encoding="utf-8"))
        item = next(p for p in spec["family_parameters"] if p["name"] == "x1")
        item["formula"] = "-l3 / 3"
        item["source"]["calculation"] = item["formula"]
        spec["geometry"][0].pop("placement_source")
        before = {"current_type": "base", "types": ["base"], "parameters": [], "existing_geometry": {"has_existing_geometry": False}}
        plan = production_plan(before, "build", spec)
        self.assertFalse(plan["ready"])
        self.assertTrue(any("Cylinder start/end" in e for e in plan["errors"]))
        self.assertTrue(any("traceability" in e for e in plan["errors"]))
