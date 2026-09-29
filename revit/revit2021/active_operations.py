# -*- coding: utf-8 -*-
"""Conservative active-family writes. No document creation, connector edits or deletion."""
import hashlib
import io
import json
import math
import os
import re
import traceback
import uuid

from System import DateTime, Double, Int32
from pyrevit import DB
from core.active_plan import parameter_plan, UNIT_TYPES, finite
from core.builder_plan import production_plan
from core.geometry_parameters import resolve_dimensions
from core.parameter_policy import is_information_parameter, information_status
from revit.revit2021 import active_family as audit
from revit.revit2021 import geometry_service as geometry
from revit.revit2021.unit_converter import UnitConverter as Units

TEST_PARAMETERS = {"width": u"MCP_Ширина", "depth": u"MCP_Глубина", "height": u"MCP_Высота"}
FLEX = [[800.0, 600.0, 1200.0], [1000.0, 800.0, 1500.0], [600.0, 400.0, 900.0]]


class Failures(DB.IFailuresPreprocessor):
    def __init__(self, result):
        self.result = result

    def PreprocessFailures(self, accessor):
        failed = False
        for message in accessor.GetFailureMessages():
            if message.GetSeverity() == DB.FailureSeverity.Warning:
                self.result["warnings"].append(message.GetDescriptionText())
                accessor.DeleteWarning(message)
            else:
                self.result["errors"].append(message.GetDescriptionText())
                failed = True
        return DB.FailureProcessingResult.ProceedWithRollBack if failed else DB.FailureProcessingResult.Continue


def transaction(doc, name, action, result):
    tx = DB.Transaction(doc, name)
    try:
        audit.require(tx.Start() == DB.TransactionStatus.Started, "Could not start " + name)
        options = tx.GetFailureHandlingOptions()
        options.SetClearAfterRollback(True)
        options.SetFailuresPreprocessor(Failures(result))
        tx.SetFailureHandlingOptions(options)
        value = action()
        audit.require(tx.Commit() == DB.TransactionStatus.Committed, "Transaction rolled back: " + name)
        return value
    finally:
        if tx.GetStatus() == DB.TransactionStatus.Started:
            tx.RollBack()
        tx.Dispose()


def same(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(same(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(left - right) <= 1e-9 * max(1, abs(left), abs(right))
    return left == right


def preserved(doc, before, allowed_values):
    current = {p["id"]: p for p in audit.parameters(doc)}
    recalculated = []
    written_types = set(t for t, name in allowed_values)
    for original in before["parameters"]:
        audit.require(original["id"] in current, "Original parameter was removed")
        actual = current[original["id"]]
        for key in ("name", "guid", "is_shared", "is_instance", "parameter_type", "parameter_group", "formula"):
            audit.require(original[key] == actual[key], "Original parameter metadata changed: " + original["name"])
        for type_name, value in original["values_by_type_internal"].items():
            if original["formula"] and type_name in written_types:
                actual_value = actual["values_by_type_internal"].get(type_name)
                if not same(value, actual_value):
                    recalculated.append({"parameter": original["name"], "family_type": type_name,
                                         "before_internal": value, "after_internal": actual_value,
                                         "formula": original["formula"], "action": "RECALCULATED_BY_REVIT"})
                continue
            if (type_name, original["name"]) not in allowed_values:
                audit.require(same(value, actual["values_by_type_internal"].get(type_name)),
                              "Unrequested original value changed: " + original["name"])
    audit.require(doc.OwnerFamily.FamilyCategory.Id.IntegerValue == before["category_id"], "Family category changed")
    audit.require(set(before["types"]).issubset(set(t.Name for t in doc.FamilyManager.Types)), "Original type was removed")
    connectors = audit.connector_snapshot(doc)
    audit.require(same(before["connectors"], connectors), "Connector identity, position or settings changed")
    old_geometry = before["existing_geometry"]["elements"]
    new_geometry = {e["unique_id"]: e for e in audit.existing_geometry(doc)["elements"]}
    for element in old_geometry:
        audit.require(element["unique_id"] in new_geometry and same(element, new_geometry[element["unique_id"]]),
                      "Original geometry changed")
    planes = {p["unique_id"]: p for p in audit.reference_planes(doc)}
    for plane in before["reference_planes"]:
        audit.require(plane["unique_id"] in planes and same(plane, planes[plane["unique_id"]]), "Original reference plane changed")
    return {"category_unchanged": True, "original_parameters_preserved": True,
            "shared_guids_unchanged": True, "formulas_unchanged": True,
            "original_types_preserved": True, "original_geometry_preserved": True,
            "original_reference_planes_preserved": True, "connectors_unchanged": True,
            "formula_value_recalculations": recalculated,
            "connector_count_before": len(before["connectors"]), "connector_count_after": len(connectors)}


def hash_file(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def save_as(doc, path):
    options = DB.SaveAsOptions()
    try:
        options.OverwriteExistingFile = False
        options.MaximumBackups = 1
        doc.SaveAs(path, options)
    finally:
        options.Dispose()


def export_preview(doc, output_dir):
    from System.Collections.Generic import List
    views = [v for v in audit.collect(doc, DB.View3D) if not v.IsTemplate and not v.IsPerspective]
    if not views:
        return {"available": False, "reason": "No existing orthographic 3D view"}
    view = sorted(views, key=lambda v: v.Id.IntegerValue)[0]
    options = DB.ImageExportOptions()
    try:
        options.FilePath = os.path.join(output_dir, "preview")
        options.ExportRange = DB.ExportRange.SetOfViews
        options.SetViewsAndSheets(List[DB.ElementId]([view.Id]))
        options.ZoomType = DB.ZoomFitType.FitToPage
        options.PixelSize = 1800
        options.HLRandWFViewsFileType = DB.ImageFileType.PNG
        options.ShadowViewsFileType = DB.ImageFileType.PNG
        doc.ExportImage(options)
        return {"available": True, "view_id": view.Id.IntegerValue,
                "paths": [os.path.join(output_dir, n) for n in os.listdir(output_dir)
                          if n.startswith("preview") and n.lower().endswith(".png")]}
    finally:
        options.Dispose()


def find_parameter(doc, name):
    matches = [p for p in doc.FamilyManager.Parameters if p.Definition.Name == name]
    audit.require(len(matches) == 1, "Parameter missing or ambiguous: " + name)
    return matches[0]


def set_value(doc, entry):
    manager = doc.FamilyManager
    parameter = find_parameter(doc, entry["parameter"])
    audit.require((not parameter.IsInstance or entry.get("allow_instance_default") is True)
                  and not parameter.IsReadOnly and not parameter.IsReporting and not parameter.Formula,
                  "Parameter is protected")
    descriptor = {"name": parameter.Definition.Name, "is_shared": bool(parameter.IsShared),
                  "guid": str(parameter.GUID) if parameter.IsShared else None}
    if is_information_parameter(descriptor):
        status, confidence = information_status({"source": entry.get("source"),
                                                 "confidence": entry.get("confidence")}, [], descriptor)
        audit.require(status == "CONFIRMED" and entry.get("action") == "WRITE",
                      "Information parameter has no confirmed source/confidence")
    audit.require(str(parameter.Definition.ParameterType) == entry["parameter_type"], "Parameter type changed")
    value = entry["new"]
    if entry["parameter_type"] in ("Text", "URL"):
        manager.Set(parameter, value)
    elif entry["parameter_type"] in ("Integer", "YesNo"):
        manager.Set(parameter, Int32(value))
    else:
        if entry["parameter_type"] != "Number":
            enum_name = UNIT_TYPES[entry["parameter_type"]][entry["unit"]]
            audit.require(hasattr(DB.DisplayUnitType, enum_name), "Unit is unavailable in Revit 2021")
            unit = getattr(DB.DisplayUnitType, enum_name)
            value = DB.UnitUtils.ConvertToInternalUnits(Double(value), unit)
        manager.Set(parameter, Double(value))
    doc.Regenerate()
    readback = audit.raw_value(manager.CurrentType, parameter)
    if entry["parameter_type"] in UNIT_TYPES:
        unit = getattr(DB.DisplayUnitType, UNIT_TYPES[entry["parameter_type"]][entry["unit"]])
        readback = float(DB.UnitUtils.ConvertFromInternalUnits(Double(readback), unit))
    delta = abs(readback - entry["new"]) if isinstance(entry["new"], (int, float)) else None
    valid = delta <= max(1e-6, abs(entry["new"]) * 1e-9) if delta is not None else readback == entry["new"]
    audit.require(valid, "Parameter readback mismatch: " + entry["parameter"])
    return {"parameter": entry["parameter"], "family_type": manager.CurrentType.Name,
            "requested": entry["new"], "written": entry["new"], "readback": readback,
            "delta": delta, "unit": entry.get("unit"), "source": entry["source"], "status": "verified"}


def build_plan(before, operation, data, doc):
    result = {"success": True, "dry_run": data.get("dry_run", True), "ready": before["ready"],
              "active_rfa_path": before["document_path"], "family_category": before["category"],
              "context_token": before["context_token"], "preflight": before,
              "parameters_to_create": [], "parameters_to_write": [], "parameters_unmapped": [],
              "family_parameters_to_create": [], "family_parameters_reused": [], "formula_order": [],
              "adsk_parameters_to_write": [], "adsk_parameters_for_review": [], "adsk_parameters_skipped": [],
              "geometry_to_create": [], "types_to_create": [], "warnings": list(before["warnings"]),
              "errors": list(before["errors"]), "connectors_untouched": before["connector_count"],
              "parameter_count_before": before["parameter_count"], "adsk_parameter_count": before["adsk_parameter_count"],
              "connector_count_before": before["connector_count"], "connector_count_after": before["connector_count"],
              "run_id": str(uuid.uuid4()), "transaction_group_rolled_back": False, "model_committed": False,
              "saved": False, "flex_test": [], "parameter_readback": []}
    if operation == "test-geometry":
        result["test"] = "active_family_geometry"
        result["geometry_to_create"] = [{"kind": "box", "logical_id": "integration/main_body",
                                         "dimensions_mm": FLEX[0], "parameters": TEST_PARAMETERS}]
        result["flex_sequence_mm"] = FLEX
        result["mode"] = "CURRENT_TYPE"
        result["target_types"] = [before["current_type"]]
        if not before["current_type"]:
            result["types_to_create"] = ["MCP_GEOMETRY_TEST"]
            result["target_types"] = ["MCP_GEOMETRY_TEST"]
        for name in TEST_PARAMETERS.values():
            matches = [p for p in before["parameters"] if p["name"] == name]
            if not matches:
                result["parameters_to_create"].append(name)
            elif len(matches) != 1 or matches[0]["is_shared"] or matches[0]["parameter_type"] != "Length" or matches[0]["write_status"] != "writable_type_parameter":
                result["errors"].append("Existing test parameter cannot be reused: " + name)
    else:
        spec = data.get("values", {}) if operation == "set-parameters" else data.get("spec", {})
        planned = production_plan(before, operation, spec)
        result["errors"].extend(planned.pop("errors"))
        result["warnings"].extend(planned.pop("warnings"))
        result.update(planned)
    identifiers = [body.get("logical_id") for body in result["geometry_to_create"]]
    if any(not isinstance(x, (str, unicode)) or not x for x in identifiers) or len(set(identifiers)) != len(identifiers):
        result["errors"].append("Unique nonempty logical_id required")
    occupied = set(item["logical_id"] for item in geometry.generated(doc))
    if occupied.intersection(identifiers):
        result["errors"].append("Generated logical_id already exists; automatic replacement is disabled")
    result["ready"] = not result["errors"]
    return result


def dispatch(uiapp, operation, data, project_root):
    audit.require(operation in ("test-geometry", "set-parameters", "build"), "Unknown operation")
    audit.require(type(data.get("dry_run", True)) is bool, "dry_run must be boolean")
    before = audit.preflight(uiapp)
    doc = audit.active_document(uiapp)
    result = build_plan(before, operation, data, doc)
    if data.get("dry_run", True):
        return result  # No Transaction, Save, SaveAs or model changes in this branch.
    audit.require(data.get("context_token") == before["context_token"], "Stale family context; run preflight/dry-run again")
    if not result["ready"]:
        result["success"] = False
        return result
    if not any(result[k] for k in ("parameters_to_create", "parameters_to_write", "types_to_create", "geometry_to_create")):
        result["stage"] = "no_changes"
        return result  # All informational values may have been skipped; no SaveAs or Transaction.
    group = None
    original_path = doc.PathName
    original_hash = hash_file(original_path) if original_path and os.path.isfile(original_path) else None
    run_id = result["run_id"]
    backup_dir = os.path.join(project_root, "backup", run_id)
    output_dir = os.path.join(project_root, "tests", "output", run_id)
    os.makedirs(backup_dir)
    os.makedirs(output_dir)
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', doc.Title)
    backup_path = os.path.join(backup_dir, safe_name + "_before_MCP.rfa")
    output_path = os.path.join(output_dir, safe_name + "_MCP.rfa")
    if result.get("equipment", {}).get("model"):
        output_name = re.sub(r'[^\w .-]', '_', result["equipment"].get("manufacturer", "") + "_" + result["equipment"]["model"])
        output_path = os.path.join(output_dir, output_name[:100] + ".rfa")
    result.update({"backup_path": backup_path, "output_path": output_path,
                   "dry_run": False, "stage": "backup", "created_geometry_elements": []})
    allowed_values = set()
    try:
        # SaveAs captures the CURRENT in-memory document including unsaved changes.
        # Original disk file stays untouched. Active document remains open throughout.
        save_as(doc, backup_path)
        audit.require(os.path.isfile(backup_path), "Backup was not saved")
        result["backup_saved"] = True
        group = DB.TransactionGroup(doc, "FamilyMCP active family")
        audit.require(group.Start() == DB.TransactionStatus.Started, "Could not start TransactionGroup")
        result["stage"] = "parameters_and_types"
        manager = doc.FamilyManager
        target_name = result["target_types"][0]
        def create_parameters_types():
            for name in result["types_to_create"]:
                manager.NewType(name)
            for name in result["parameters_to_create"]:
                definition = next((p for p in result["family_parameters_to_create"] if p["name"] == name), None)
                kind = getattr(DB.ParameterType, definition["type"]) if definition else DB.ParameterType.Length
                manager.AddParameter(name, DB.BuiltInParameterGroup.PG_GEOMETRY, kind, False)
            if operation != "test-geometry":
                for family_type in manager.Types:
                    manager.CurrentType = family_type
                    for definition in result["family_parameters_to_create"]:
                        if not definition.get("formula"):
                            entry = {"parameter": definition["name"], "parameter_type": definition["type"],
                                     "new": definition["value"], "unit": definition.get("unit"),
                                     "source": definition["source"]}
                            result["parameter_readback"].append(set_value(doc, entry))
                for name in result["formula_order"]:
                    definition = next((p for p in result["family_parameters_to_create"] if p["name"] == name), None)
                    if definition:
                        manager.SetFormula(find_parameter(doc, name), definition["formula"])
            if operation == "test-geometry":
                # Every original type gets valid defaults for NEW parameters only.
                for family_type in manager.Types:
                    manager.CurrentType = family_type
                    for axis, value in zip(("width", "depth", "height"), FLEX[0]):
                        name = TEST_PARAMETERS[axis]
                        if name in result["parameters_to_create"]:
                            manager.Set(find_parameter(doc, name), Double(Units.mm_to_internal(value)))
            manager.CurrentType = next(t for t in manager.Types if t.Name == target_name)
            if operation == "test-geometry":
                for axis, value in zip(("width", "depth", "height"), FLEX[0]):
                    name = TEST_PARAMETERS[axis]
                    manager.Set(find_parameter(doc, name), Double(Units.mm_to_internal(value)))
                    allowed_values.add((target_name, name))
            doc.Regenerate()
        transaction(doc, "FamilyMCP parameters/types", create_parameters_types, result)

        result["stage"] = "parameter_values"
        def write_parameters():
            for type_name in result["target_types"]:
                manager.CurrentType = next(t for t in manager.Types if t.Name == type_name)
                for entry in result["parameters_to_write"]:
                    if entry["family_type"] == type_name:
                        if entry.get("allow_instance_default"):
                            affected_types = list(manager.Types)
                            for affected in affected_types:
                                manager.CurrentType = affected
                                result["parameter_readback"].append(set_value(doc, entry))
                                allowed_values.add((affected.Name, entry["parameter"]))
                            manager.CurrentType = next(t for t in manager.Types if t.Name == type_name)
                            doc.Regenerate()
                            parameter = find_parameter(doc, entry["parameter"])
                            expected = audit.raw_value(manager.CurrentType, parameter)
                            for affected in manager.Types:
                                actual = audit.raw_value(affected, parameter)
                                audit.require(same(expected, actual),
                                              "Instance default readback differs in type: " + affected.Name +
                                              "; expected=" + repr(expected) + "; actual=" + repr(actual))
                                result.setdefault("instance_default_readback", []).append({
                                    "parameter": entry["parameter"], "family_type": affected.Name,
                                    "expected_internal": expected, "readback_internal": actual,
                                    "write_scope": "family_instance_default_all_types", "status": "verified"})
                        else:
                            result["parameter_readback"].append(set_value(doc, entry))
                            allowed_values.add((type_name, entry["parameter"]))
            manager.CurrentType = next(t for t in manager.Types if t.Name == target_name)
            doc.Regenerate()
        if result["parameters_to_write"]:
            transaction(doc, "FamilyMCP parameter values", write_parameters, result)

        result["stage"] = "geometry"
        bodies = []
        def create_geometry():
            for body in result["geometry_to_create"]:
                parameters = {axis: find_parameter(doc, name) for axis, name in body["parameters"].items()}
                if operation != "test-geometry" and body["kind"] == "box":
                    for axis, value in zip(("width", "depth", "height"), body["dimensions_mm"]):
                        parameter = parameters[axis]
                        audit.require(not parameter.IsShared and not is_information_parameter({"name": parameter.Definition.Name}),
                                      "Geometry cannot drive ADSK/shared parameters")
                        actual = Units.internal_to_mm(manager.CurrentType.AsDouble(parameter))
                        audit.require(abs(actual - value) <= 0.001, "Geometry parameter differs from planned dimension")
                creator = geometry.create_cylinder if body["kind"] == "cylinder" else geometry.create_parametric_box
                extrusion, auxiliary = creator(
                    doc, geometry.body_coordinate(before["coordinate_system"], body), parameters,
                    body["dimensions_mm"], body["logical_id"], run_id)
                bodies.append((body, extrusion))
                result["created_geometry_elements"].append({"unique_id": extrusion.UniqueId,
                    "element_id": extrusion.Id.IntegerValue, "logical_id": body["logical_id"],
                    "created_by": "FamilyMCP", "spec_version": "1", "kind": "Extrusion",
                    "primitive": body["kind"], "support_element_count": len(auxiliary)})
            doc.Regenerate()
        if result["geometry_to_create"]:
            transaction(doc, "FamilyMCP reference and model geometry", create_geometry, result)

        result["stage"] = "flex_test"
        if operation == "test-geometry":
            for dimensions in FLEX:
                def flex():
                    for axis, value in zip(("width", "depth", "height"), dimensions):
                        manager.Set(find_parameter(doc, TEST_PARAMETERS[axis]), Double(Units.mm_to_internal(value)))
                    doc.Regenerate()
                    readback = [float(Units.internal_to_mm(manager.CurrentType.AsDouble(find_parameter(doc, TEST_PARAMETERS[axis]))))
                                for axis in ("width", "depth", "height")]
                    audit.require(max(abs(a - b) for a, b in zip(readback, dimensions)) <= 0.001, "Flex parameter readback mismatch")
                    measured = geometry.measure_box(bodies[0][1], before["coordinate_system"], dimensions)
                    measured["parameter_readback_mm"] = readback
                    return measured
                measured = transaction(doc, "FamilyMCP flex " + str(dimensions), flex, result)
                measured["preservation"] = preserved(doc, before, allowed_values)
                result["flex_test"].append(measured)
        else:
            for type_name in result["target_types"]:
                def check_type():
                    manager.CurrentType = next(t for t in manager.Types if t.Name == type_name)
                    doc.Regenerate()
                    for definition in result["family_parameters_to_create"] + result["family_parameters_reused"]:
                        parameter = find_parameter(doc, definition["name"])
                        audit.require(not parameter.IsShared and not parameter.IsInstance,
                                      "Geometry parameter became shared/instance")
                        audit.require((parameter.Formula or None) == (definition.get("formula") or None),
                                      "Geometry formula differs from plan")
                        expected = result["expected_geometry_parameters_by_type"][type_name][definition["name"]]
                        actual = audit.raw_value(manager.CurrentType, parameter)
                        if definition["type"] in ("Length", "Angle"):
                            unit = DB.DisplayUnitType.DUT_MILLIMETERS if definition["type"] == "Length" else DB.DisplayUnitType.DUT_DECIMAL_DEGREES
                            actual = float(DB.UnitUtils.ConvertFromInternalUnits(Double(actual), unit))
                        audit.require(abs(actual - expected) <= 0.001, "Geometry parameter/formula readback mismatch")
                        result["parameter_readback"].append({"parameter": definition["name"], "family_type": type_name,
                            "requested": expected, "readback": actual, "formula": definition.get("formula"),
                            "source": definition["source"], "status": "verified"})
                    measured = []
                    for body, element in bodies:
                        requested = body["dimensions_by_type_mm"][type_name]
                        item = geometry.measure_box(element, geometry.body_coordinate(before["coordinate_system"], body), requested,
                                                    cylinder=body["kind"] == "cylinder")
                        item["logical_id"] = body["logical_id"]
                        item["family_type"] = type_name
                        measured.append(item)
                    return measured
                result["flex_test"].extend(transaction(doc, "FamilyMCP validate type", check_type, result))
            def restore_type():
                manager.CurrentType = next(t for t in manager.Types if t.Name == target_name)
                doc.Regenerate()
            transaction(doc, "FamilyMCP restore selected type", restore_type, result)
            for check in result.get("flex_checks", []):
                definitions = result["family_parameters_to_create"] + result["family_parameters_reused"]
                defaults = result["expected_geometry_parameters_by_type"][target_name]
                original = {}
                def run_flex():
                    overrides = {name: {"value": value} for name, value in defaults.items()}
                    for name, value in check["values_mm"].items():
                        definition = next((p for p in definitions if p["name"] == name), None)
                        audit.require(definition is not None and definition["type"] == "Length" and not definition.get("formula"),
                                      "Flex can only change declared independent Length parameters")
                        parameter = find_parameter(doc, name)
                        audit.require(not parameter.IsShared and not parameter.IsInstance and not parameter.Formula,
                                      "Flex cannot change information parameters")
                        original[name] = manager.CurrentType.AsDouble(parameter)
                        manager.Set(parameter, Double(Units.mm_to_internal(value)))
                        overrides[name] = {"value": value}
                    expected = resolve_dimensions(definitions, overrides)
                    doc.Regenerate()
                    measured = []
                    for body, element in bodies:
                        p = body["parameters"]
                        sizes = ([expected[p[k]] for k in ("width", "depth", "height")] if body["kind"] == "box" else
                                 [expected[p["length"]], 2 * expected[p["radius"]], 2 * expected[p["radius"]]])
                        item = geometry.measure_box(element, geometry.body_coordinate(before["coordinate_system"], body), sizes,
                                                    cylinder=body["kind"] == "cylinder")
                        item["logical_id"] = body["logical_id"]
                        measured.append(item)
                    return {"check": check, "measurements": measured, "success": True}
                outcome = transaction(doc, "FamilyMCP production flex", run_flex, result)
                def restore_flex():
                    for name, value in original.items():
                        manager.Set(find_parameter(doc, name), Double(value))
                    doc.Regenerate()
                    for body, element in bodies:
                        geometry.measure_box(element, geometry.body_coordinate(before["coordinate_system"], body),
                                             body["dimensions_by_type_mm"][target_name], cylinder=body["kind"] == "cylinder")
                transaction(doc, "FamilyMCP restore production dimensions", restore_flex, result)
                outcome["restored"] = True
                result["flex_test"].append(outcome)
        result["stage"] = "validate_preservation"
        if bodies and result.get("overall_dimensions_mm"):
            boxes = [audit.box_data(element) for body, element in bodies]
            minimum = [min(box["min_mm"][i] for box in boxes) for i in range(3)]
            maximum = [max(box["max_mm"][i] for box in boxes) for i in range(3)]
            actual = [maximum[i] - minimum[i] for i in range(3)]
            expected = result["overall_dimensions_mm"]
            audit.require(max(abs(a - b) for a, b in zip(actual, expected)) <= 0.001, "Assembly overall dimensions mismatch")
            origin = before["coordinate_system"]["origin_mm"]
            audit.require(abs(minimum[2] - origin[2]) <= 0.001 and
                          all(abs((minimum[i] + maximum[i]) / 2 - origin[i]) <= 0.001 for i in (0, 1)),
                          "Assembly origin mismatch")
            result["assembly_validation"] = {"success": True, "size_mm": actual, "expected_mm": expected,
                                             "min_mm": minimum, "max_mm": maximum}
        result["validation"] = preserved(doc, before, allowed_values)
        result["parameter_count_after"] = doc.FamilyManager.Parameters.Size
        result["connector_count_after"] = len(audit.collect(doc, DB.ConnectorElement))
        audit.require(group.Assimilate() == DB.TransactionStatus.Committed, "TransactionGroup failed to commit")
        result["model_committed"] = True
        group.Dispose()
        group = None
        result["stage"] = "save_result"
        save_as(doc, output_path)
        result["saved"] = True
        if operation == "build":
            try:
                result["preview"] = export_preview(doc, output_dir)
            except Exception as error:
                result["preview"] = {"available": False, "error": unicode(error)}
        result["active_rfa_path_after"] = doc.PathName
        result["original_file_unchanged"] = original_hash is None or hash_file(original_path) == original_hash
        audit.require(result["original_file_unchanged"], "Original disk file changed")
        result["stage"] = "complete"
        result["success"] = not result["errors"]
    except Exception as error:
        result.update({"success": False, "error_type": type(error).__name__, "error": unicode(error),
                       "traceback": traceback.format_exc()})
        result["errors"].append(unicode(error))
    finally:
        if group is not None:
            try:
                if group.GetStatus() == DB.TransactionStatus.Started:
                    result["transaction_group_rolled_back"] = group.RollBack() == DB.TransactionStatus.RolledBack
                group.Dispose()
            except Exception as error:
                result["success"] = False
                result["errors"].append("Rollback: " + unicode(error))
                result["cleanup_traceback"] = traceback.format_exc()
        result["active_rfa_path_after"] = doc.PathName
        result["finished_at_utc"] = DateTime.UtcNow.ToString("o")
        result["log_path"] = os.path.join(output_dir, "active-family-result.json")
        with io.open(result["log_path"], "w", encoding="utf-8") as stream:
            stream.write(unicode(json.dumps(result, ensure_ascii=False, indent=2)))
    return result
