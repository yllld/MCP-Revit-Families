"""Conservative write planning tests; no Revit or filesystem mutations."""
import copy
import unittest
from core.active_plan import parameter_plan


class ActivePlanTests(unittest.TestCase):
    def setUp(self):
        self.audit = {"current_type": "A", "types": ["A"], "parameters": [
            {"name": "ADSK_Масса", "id": 42, "parameter_type": "Mass", "guid": "guid-1",
             "write_status": "writable_type_parameter", "values_by_type_internal": {"A": 1.0}}]}
        self.payload = {"values": {"ADSK_Масса": {"value": 125, "unit": "kg", "confidence": 1.0,
                         "source": {"source_type": "direct", "file": "catalog.pdf", "page": 17, "field": "Масса",
                                    "origin": "user_provided", "model_match": True}}}}

    def assertSkipped(self, plan):
        self.assertEqual(plan["parameters_to_write"], [])
        self.assertTrue(plan["adsk_parameters_skipped"] or plan["adsk_parameters_for_review"])

    def test_exact_existing_parameter_with_source_and_units(self):
        plan = parameter_plan(self.audit, self.payload)
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["parameters_to_write"][0]["source"]["page"], 17)

    def test_unknown_target_never_creates_adsk(self):
        payload = {"values": {"ADSK_Новый": self.payload["values"]["ADSK_Масса"]}}
        plan = parameter_plan(self.audit, payload)
        self.assertSkipped(plan)
        self.assertEqual(plan["parameters_to_write"], [])
        self.assertEqual(plan["parameters_unmapped"][0]["status"], "PARAMETER_NOT_FOUND")

    def test_formula_instance_and_readonly_protected(self):
        for status in ("controlled_by_formula", "instance_default_not_supported", "read_only"):
            self.audit["parameters"][0]["write_status"] = status
            plan = parameter_plan(self.audit, self.payload)
            self.assertSkipped(plan)
            self.assertEqual(plan["adsk_parameters_skipped"][0]["status"], status)

    def test_never_guesses_units_or_missing_values(self):
        for value, unit in ((125, None), (125, "kW"), (None, "kg"), (float("nan"), "kg"), (float("inf"), "kg")):
            payload = copy.deepcopy(self.payload)
            payload["values"]["ADSK_Масса"].update(value=value, unit=unit)
            self.assertSkipped(parameter_plan(self.audit, payload))

    def test_missing_source_blocked(self):
        self.payload["values"]["ADSK_Масса"]["source"]["source_type"] = "missing"
        self.assertSkipped(parameter_plan(self.audit, self.payload))

    def test_ambiguous_or_wrong_guid_mapping_requires_review(self):
        self.payload["parameter_mapping"] = {"mappings": [{"source": "Масса", "target": "ADSK_Масса", "confidence": 0.94}]}
        self.assertSkipped(parameter_plan(self.audit, self.payload))
        mapping = self.payload["parameter_mapping"]["mappings"][0]
        mapping["confirmed"] = True
        self.assertSkipped(parameter_plan(self.audit, self.payload))
        mapping["confidence"] = 0.95
        self.assertEqual(len(parameter_plan(self.audit, self.payload)["adsk_parameters_to_write"]), 1)
        mapping["guid"] = "different-guid"
        self.assertSkipped(parameter_plan(self.audit, self.payload))

    def test_duplicate_names_are_unmapped(self):
        self.audit["parameters"].append(copy.deepcopy(self.audit["parameters"][0]))
        self.assertSkipped(parameter_plan(self.audit, self.payload))

    def test_new_type_requires_explicit_catalog_mode(self):
        self.payload["family_type"] = "B"
        self.assertFalse(parameter_plan(self.audit, self.payload)["ready"])
        self.assertTrue(parameter_plan(self.audit, self.payload, allow_new_type=True)["ready"])
