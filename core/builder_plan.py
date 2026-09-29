# -*- coding: utf-8 -*-
"""Reviewable production plan; geometry and information writes are disjoint."""
import copy
from core.active_plan import parameter_plan, finite
from core.geometry_parameters import geometry_parameter_plan, resolve_dimensions
from core.parameter_policy import POLICY_VERSION, is_information_parameter


def production_plan(before, operation, spec):
    result = {"policy_version": POLICY_VERSION, "errors": [], "warnings": [],
              "parameters_to_create": [], "parameters_to_write": [], "parameters_unmapped": [],
              "adsk_parameters_to_write": [], "adsk_parameters_for_review": [], "adsk_parameters_skipped": [],
              "family_parameters_to_create": [], "family_parameters_reused": [], "formula_order": [],
              "types_to_create": [], "target_types": [], "geometry_to_create": [],
              "expected_geometry_parameters_by_type": {}}
    for key in ("equipment", "technical_data", "source_documents", "geometry_omitted", "simplifications", "overall_dimensions_mm", "flex_checks"):
        if key in spec:
            result[key] = copy.deepcopy(spec[key])
    declarations = spec.get("family_parameters", [])
    if operation != "build" and declarations:
        result["errors"].append("Only the builder can create geometry parameters")
        declarations = []
    family = geometry_parameter_plan(before, declarations)
    result["errors"].extend(family["errors"])
    for key in ("family_parameters_to_create", "family_parameters_reused", "formula_order"):
        result[key] = family[key]
    definitions = family["family_parameters_to_create"] + family["family_parameters_reused"]
    result["geometry_parameter_mapping"] = [{"parameter": p["name"], "meaning": p["purpose"],
        "source": p["source"], "naming": p["naming"], "formula": p.get("formula")} for p in definitions]
    result["parameters_to_create"] = [p["name"] for p in family["family_parameters_to_create"]]
    result["mode"] = mode = spec.get("mode", "CURRENT_TYPE")
    if mode not in ("CURRENT_TYPE", "CATALOG_TYPES"):
        result["errors"].append("Unsupported family type mode")
    catalog = spec.get("types", []) if mode == "CATALOG_TYPES" else [spec]
    if not catalog:
        result["errors"].append("Explicit catalog types are required")
    for supplied in catalog:
        payload = copy.deepcopy(supplied)
        payload.setdefault("write_instance_defaults", spec.get("write_instance_defaults", False))
        payload.setdefault("parameter_mapping", spec.get("parameter_mapping", {"mappings": [], "unmapped": []}))
        values = payload.setdefault("values", {})
        for definition in definitions:
            if not definition.get("formula"):
                values.setdefault(definition["name"], {k: definition[k] for k in ("value", "source")})
                values[definition["name"]].setdefault("unit", definition.get("unit"))
        planned = parameter_plan(family["audit"], payload, allow_new_type=mode == "CATALOG_TYPES")
        target = planned["family_type"]
        result["target_types"].append(target)
        for key in ("parameters_to_write", "parameters_unmapped", "errors", "adsk_parameters_to_write",
                    "adsk_parameters_for_review", "adsk_parameters_skipped"):
            result[key].extend(planned[key])
        if target not in before["types"]:
            result["types_to_create"].append(target)
        try:
            result["expected_geometry_parameters_by_type"][target] = resolve_dimensions(definitions, values)
        except (ValueError, TypeError, KeyError) as error:
            result["errors"].append(str(error))
    if len(set(result["target_types"])) != len(result["target_types"]):
        result["errors"].append("Duplicate catalog type names")
    instance_defaults = {}
    for entry in result["parameters_to_write"]:
        if entry.get("allow_instance_default"):
            entry["affected_types"] = sorted(set(before["types"] + result["target_types"]))
            value = (entry["new"], entry.get("unit"))
            previous = instance_defaults.get(entry["parameter"])
            if previous is not None and previous != value:
                result["errors"].append("Conflicting family-wide instance defaults: " + entry["parameter"])
            instance_defaults[entry["parameter"]] = value
    if operation == "build":
        result["geometry_to_create"] = copy.deepcopy(spec.get("geometry", []))
    if result["geometry_to_create"] and before["existing_geometry"]["has_existing_geometry"] and spec.get("allow_add_to_existing_geometry") is not True:
        result["errors"].append("Existing geometry requires an explicit additive specification")
    declared_names = set(p["name"] for p in definitions)
    for body in result["geometry_to_create"]:
        kind = body.get("kind")
        if kind not in ("box", "cylinder"):
            result["errors"].append("Unsupported primitive: " + str(body.get("kind")))
            continue
        parameters = body.get("parameters", {})
        required = {"width", "depth", "height"} if kind == "box" else {"length", "radius", "start", "end"}
        if set(parameters) != required:
            result["errors"].append("Explicit body parameter mapping is required: " + str(sorted(required)))
            continue
        offset = body.get("offset_mm", [0, 0, 0])
        if len(offset) != 3 or not all(finite(v) and abs(v) <= 100000 for v in offset):
            result["errors"].append("Body offset must contain three finite millimeter coordinates")
        if any(offset) and not body.get("placement_source"):
            result["errors"].append("Nonzero body placement needs source traceability")
        if kind == "cylinder" and body.get("axis", "X") != "X":
            result["errors"].append("This cylinder implementation supports the X axis")
        for name in parameters.values():
            matches = [p for p in family["audit"]["parameters"] if p["name"] == name]
            if (name not in declared_names or len(matches) != 1 or is_information_parameter(matches[0]) or
                    matches[0]["parameter_type"] != "Length"):
                result["errors"].append("Body dimensions require declared ordinary Length parameters: " + name)
        by_type = {}
        for target in result["target_types"]:
            expected = result["expected_geometry_parameters_by_type"].get(target, {})
            if kind == "box":
                dimensions = [expected.get(parameters[axis]) for axis in ("width", "depth", "height")]
            else:
                length, radius = expected.get(parameters["length"]), expected.get(parameters["radius"])
                start, end = expected.get(parameters["start"]), expected.get(parameters["end"])
                dimensions = [length, 2 * radius if finite(radius) else None, 2 * radius if finite(radius) else None]
                if not all(finite(v) for v in (length, start, end)) or abs(start + length / 2) > 0.001 or abs(end - length / 2) > 0.001:
                    result["errors"].append("Cylinder start/end must equal -length/2 and length/2")
            if not all(finite(v) and 0.1 <= v <= 100000 for v in dimensions):
                result["errors"].append("Box dimensions must resolve to 0.1..100000 mm for every target type")
            by_type[target] = dimensions
        body["dimensions_by_type_mm"] = by_type
        if result["target_types"]:
            first = by_type[result["target_types"][0]]
            if "dimensions_mm" in body and body["dimensions_mm"] != first:
                result["errors"].append("Body dimensions conflict with declared geometry parameters")
            body["dimensions_mm"] = first
    # Defaults for new independent parameters on original types are explicit in declarations.
    # Their formulas may only depend on these declarations; never on guessed values.
    if definitions:
        try:
            result["new_parameter_defaults"] = resolve_dimensions(definitions, {})
        except (ValueError, TypeError, KeyError) as error:
            result["errors"].append(str(error))
    if result["adsk_parameters_for_review"] or result["adsk_parameters_skipped"]:
        result["warnings"].append("Unconfirmed/protected information parameters will remain unwritten; see review/skipped")
    result["ready"] = not result["errors"]
    return result
