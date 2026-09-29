import copy
import unittest

from core.active_plan import parameter_plan
from core.builder_plan import production_plan
from core.geometry_parameters import geometry_parameter_plan, resolve_dimensions


def source(field="dimension"):
    return {"source_type": "direct", "field": field, "file": "manufacturer.pdf", "page": 12,
            "origin": "user_provided", "model_match": True}


def dimension(name, value=800, **extra):
    return dict({"name": name, "type": "Length", "value": value, "unit": "mm",
                 "purpose": "Body dimension", "used_for": ["geometry"], "source": source()}, **extra)


def audit():
    return {"current_type": "A", "types": ["A"], "parameters": [],
            "existing_geometry": {"has_existing_geometry": False}}


def box_spec():
    return {"family_parameters": [dimension("L", 800), dimension("B", 600), dimension("H", 1200)],
            "geometry": [{"kind": "box", "logical_id": "body",
                          "parameters": {"width": "B", "depth": "L", "height": "H"}}]}


class GeometryPolicyTests(unittest.TestCase):
    def test_production_box_creates_ordinary_parameters(self):
        plan = production_plan(audit(), "build", box_spec())
        self.assertTrue(plan["ready"], plan["errors"])
        self.assertEqual(set(plan["parameters_to_create"]), {"L", "B", "H"})
        self.assertTrue(all(not p["is_shared"] for p in plan["family_parameters_to_create"]))
        self.assertEqual(plan["geometry_to_create"][0]["dimensions_mm"], [600, 800, 1200])

    def test_manufacturer_notation_has_priority(self):
        declaration = dimension("A")
        declaration["source"]["designation"] = "A"
        self.assertEqual(geometry_parameter_plan(audit(), [declaration])["errors"], [])
        declaration["name"] = "L"
        self.assertTrue(geometry_parameter_plan(audit(), [declaration])["errors"])

    def test_engineering_notation_without_designation(self):
        for name in ("L", "B", "H", "l1", "d1", "r1", "t1", "a1", "x1"):
            self.assertEqual(geometry_parameter_plan(audit(), [dimension(name)])["errors"], [])
        self.assertTrue(geometry_parameter_plan(audit(), [dimension("LongDimensionName")])["errors"])

    def test_angles_visibility_and_datatype(self):
        for name in ("α1", "a_ang1"):
            item = dimension(name, 45, type="Angle", unit="deg")
            self.assertEqual(geometry_parameter_plan(audit(), [item])["errors"], [])
        visible = dimension("v1", True, type="YesNo", used_for=["visibility"])
        self.assertEqual(geometry_parameter_plan(audit(), [visible])["errors"], [])
        self.assertTrue(geometry_parameter_plan(audit(), [dimension("L", 45, type="Angle", unit="deg")])["errors"])
        visible["used_for"] = ["geometry"]
        self.assertTrue(geometry_parameter_plan(audit(), [visible])["errors"])

    def test_no_shared_adsk_or_mcp_creation_even_with_designation(self):
        for name in ("ADSK_Масса", "АДСК_Масса", "MCP_Ширина"):
            item = dimension(name)
            item["source"]["designation"] = name
            self.assertTrue(geometry_parameter_plan(audit(), [item])["errors"])
        for extra in ({"is_shared": True}, {"guid": "some-guid"}, {"is_instance": True}):
            self.assertTrue(geometry_parameter_plan(audit(), [dimension("L", **extra)])["errors"])

    def test_geometry_cannot_drive_information_parameter(self):
        before = audit()
        before["parameters"] = [{"name": "L", "is_shared": True, "parameter_type": "Length",
                                 "write_status": "writable_type_parameter", "values_by_type_internal": {}, "id": 2}]
        plan = production_plan(before, "build", box_spec())
        self.assertFalse(plan["ready"])

    def test_formula_with_traceable_derivation_and_units(self):
        radius = dimension("r1")
        del radius["value"]
        radius.update(formula="d1 / 2", source=dict(source(), source_type="derived", calculation="d1 / 2"))
        plan = geometry_parameter_plan(audit(), [dimension("d1", 200), radius])
        self.assertEqual(plan["errors"], [])
        self.assertEqual(plan["formula_order"], ["r1"])
        self.assertEqual(resolve_dimensions(plan["family_parameters_to_create"], {})["r1"], 100)

    def test_formula_cycles_unknown_dependencies_and_code_rejected(self):
        for formula in ("r1 / 2", "missing / 2", "__import__('os')"):
            item = dimension("r1")
            del item["value"]
            item.update(formula=formula, source=dict(source(), source_type="derived", calculation=formula))
            self.assertTrue(geometry_parameter_plan(audit(), [item])["errors"])

    def test_formula_type_and_zero_division_rejected(self):
        for formula in ("L * L", "L / 0", "L + 1"):
            item = dimension("r1")
            del item["value"]
            item.update(formula=formula, source=dict(source(), source_type="derived", calculation=formula))
            with self.assertRaises(ValueError):
                resolve_dimensions([dimension("L"), item], {})

    def test_existing_formula_never_overwritten(self):
        before = audit()
        before["parameters"] = [{"name": "L", "parameter_type": "Length", "formula": "B / 2"}]
        self.assertTrue(geometry_parameter_plan(before, [dimension("L")])["errors"])

    def test_unknown_dimension_is_not_invented(self):
        spec = box_spec()
        spec["family_parameters"][0]["source"]["source_type"] = "UNKNOWN"
        self.assertFalse(production_plan(audit(), "build", spec)["ready"])

    def test_catalog_dimensions_resolve_per_type(self):
        spec = box_spec()
        spec.update(mode="CATALOG_TYPES", types=[{"family_type": "A"}, {"family_type": "B", "values": {
            "L": {"value": 1000, "unit": "mm", "source": source()}}}])
        plan = production_plan(audit(), "build", spec)
        self.assertTrue(plan["ready"], plan["errors"])
        self.assertEqual(plan["geometry_to_create"][0]["dimensions_by_type_mm"]["B"], [600, 1000, 1200])

    def test_conflicting_duplicate_dimensions_rejected(self):
        spec = box_spec()
        spec["geometry"][0]["dimensions_mm"] = [1, 2, 3]
        self.assertFalse(production_plan(audit(), "build", spec)["ready"])


class InformationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.before = audit()
        self.before["parameters"] = [{"name": "ADSK_Масса", "id": 1, "is_shared": True,
            "parameter_type": "Mass", "values_by_type_internal": {"A": 0}, "write_status": "writable_type_parameter"}]
        self.value = {"value": 125, "unit": "kg", "source": source("mass"), "confidence": 1.0}

    def plan(self):
        return parameter_plan(self.before, {"values": {"ADSK_Масса": self.value}})

    def test_confidence_boundaries(self):
        for score, bucket in ((1, "to_write"), (.95, "to_write"), (.949, "for_review"), (.70, "for_review"), (.699, "skipped")):
            self.value["confidence"] = score
            result = self.plan()
            self.assertEqual(len(result["adsk_parameters_" + bucket]), 1)
            self.assertEqual(len(result["parameters_to_write"]), int(bucket == "to_write"))

    def test_missing_invalid_confidence_cannot_bypass(self):
        for score in (None, True, 1.1, float("nan"), float("inf")):
            self.value["confidence"] = score
            self.assertEqual(self.plan()["parameters_to_write"], [])

    def test_source_guards(self):
        for change in ({"source_type": "derived", "calculation": "250 / 2"}, {"source_type": "missing"},
                       {"model_match": False}, {"origin": "external"}, {"file": None}, {"origin": None}):
            self.value["source"] = dict(source(), **change)
            self.assertEqual(self.plan()["parameters_to_write"], [])

    def test_conflict_reports_both_values_even_without_confidence(self):
        self.value["confidence"] = None
        self.value["source"]["conflicts"] = [{"file": "one.pdf", "value": 125}, {"file": "two.pdf", "value": 130}]
        entry = self.plan()["adsk_parameters_skipped"][0]
        self.assertEqual(entry["status"], "SOURCE_CONFLICT")
        self.assertEqual(len(entry["conflicts"]), 2)

    def test_shared_parameter_without_adsk_name_uses_same_policy(self):
        self.before["parameters"][0]["name"] = "ManufacturerMass"
        self.value["confidence"] = .94
        result = parameter_plan(self.before, {"values": {"ManufacturerMass": self.value}})
        self.assertEqual(result["parameters_to_write"], [])
        self.assertEqual(len(result["adsk_parameters_for_review"]), 1)

    def test_instance_default_requires_explicit_opt_in_and_preserves_formula_guard(self):
        p = self.before["parameters"][0]
        p.update(is_instance=True, is_read_only=False, is_reporting=False, formula=None,
                 write_status="instance_default_not_supported")
        self.assertEqual(self.plan()["parameters_to_write"], [])
        payload = {"write_instance_defaults": True, "values": {"ADSK_Масса": self.value}}
        result = parameter_plan(self.before, payload)
        self.assertEqual(len(result["parameters_to_write"]), 1)
        self.assertTrue(result["parameters_to_write"][0]["allow_instance_default"])
        self.assertEqual(result["parameters_to_write"][0]["write_scope"], "family_instance_default_all_types")
        self.assertEqual(result["parameters_to_write"][0]["affected_types"], ["A"])
        p.update(formula="2 * 5", write_status="controlled_by_formula")
        self.assertEqual(parameter_plan(self.before, payload)["parameters_to_write"], [])

    def test_catalog_rejects_conflicting_instance_defaults(self):
        self.before["parameters"][0].update(is_instance=True, is_read_only=False,
            is_reporting=False, formula=None, write_status="instance_default_requires_opt_in")
        spec = box_spec()
        spec.update(mode="CATALOG_TYPES", write_instance_defaults=True, types=[
            {"family_type": "A", "values": {"ADSK_Масса": self.value}},
            {"family_type": "B", "values": {"ADSK_Масса": dict(self.value, value=250)}}])
        plan = production_plan(self.before, "build", spec)
        self.assertFalse(plan["ready"])
        self.assertTrue(any("Conflicting family-wide" in e for e in plan["errors"]))
        self.assertEqual(plan["adsk_parameters_to_write"][0]["affected_types"], ["A", "B"])

    def test_unconfirmed_adsk_does_not_block_geometry(self):
        spec = box_spec()
        self.value["confidence"] = .8
        spec["values"] = {"ADSK_Масса": self.value}
        result = production_plan(self.before, "build", spec)
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(len(result["parameters_to_write"]), 3)
        self.assertEqual(len(result["adsk_parameters_for_review"]), 1)

    def test_adsk_mapping_does_not_require_mapping_for_geometry(self):
        spec = box_spec()
        spec["values"] = {"ADSK_Масса": self.value}
        spec["parameter_mapping"] = {"mappings": [{"target": "ADSK_Масса", "confidence": .96}],
            "unmapped": [{"source_field": "Unknown catalog field", "source_value": 12}]}
        result = production_plan(self.before, "build", spec)
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(len(result["parameters_to_write"]), 4)
        self.assertEqual(len(result["adsk_parameters_skipped"]), 1)
