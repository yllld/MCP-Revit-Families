# -*- coding: utf-8 -*-
"""Pure Python 2.7/3 planning. Never guesses a parameter or opens a transaction."""
import math
from core.parameter_policy import is_information_parameter, information_status, source_status

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)

UNIT_TYPES = {
    "Length": {"mm": "DUT_MILLIMETERS"},
    "Area": {"m2": "DUT_SQUARE_METERS", "mm2": "DUT_SQUARE_MILLIMETERS"},
    "Volume": {"m3": "DUT_CUBIC_METERS"},
    "Angle": {"deg": "DUT_DECIMAL_DEGREES"},
    "Mass": {"kg": "DUT_KILOGRAMS"},
    "ElectricalPower": {"W": "DUT_WATTS", "kW": "DUT_KILOWATTS"},
    "ElectricalPotential": {"V": "DUT_VOLTS"},
    "ElectricalFrequency": {"Hz": "DUT_HERTZ"},
    "HVACAirflow": {"m3/h": "DUT_CUBIC_METERS_PER_HOUR", "l/s": "DUT_LITERS_PER_SECOND"},
    "PipingFlow": {"m3/h": "DUT_CUBIC_METERS_PER_HOUR", "l/s": "DUT_LITERS_PER_SECOND"},
    "HVACPressure": {"Pa": "DUT_PASCALS", "kPa": "DUT_KILOPASCALS"},
    "PipingPressure": {"Pa": "DUT_PASCALS", "kPa": "DUT_KILOPASCALS"},
}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(value) and not math.isinf(value)


def parameter_plan(audit, payload, allow_new_type=False):
    name = payload.get("family_type") or audit["current_type"]
    errors, changes, unmapped = [], [], []
    information_write, review, skipped = [], [], []
    if not name or (name not in audit["types"] and not allow_new_type):
        errors.append("Target family type does not exist")
    mapping = payload.get("parameter_mapping", {"mappings": [], "unmapped": []})
    for candidate in mapping.get("unmapped", []):
        entry = dict(candidate, status="UNKNOWN", action="DO_NOT_WRITE")
        unmapped.append(entry)
        skipped.append(entry)
    mappings = mapping.get("mappings", [])
    used_targets = set()
    for target, supplied in sorted(payload.get("values", {}).items()):
        match = [p for p in audit["parameters"] if p["name"] == target]
        entry = {"parameter": target, "family_type": name, "status": "unmapped"}
        if len(match) != 1:
            entry["possible_parameters"] = [p["name"] for p in audit["parameters"] if target.lower() in p["name"].lower()]
            unmapped.append(entry)
            if is_information_parameter({"name": target}):
                entry.update({"source": supplied.get("source") if isinstance(supplied, dict) else None,
                              "status": "PARAMETER_NOT_FOUND" if not match else "AMBIGUOUS_MAPPING"})
                skipped.append(entry)
            continue
        p = match[0]
        information = is_information_parameter(p)
        allow_instance = (payload.get("write_instance_defaults") is True and p.get("is_instance") is True
                          and p.get("is_read_only") is False and p.get("is_reporting") is False and not p.get("formula"))
        entry.update({"parameter_id": p["id"], "parameter_type": p["parameter_type"],
                      "old_internal": p["values_by_type_internal"].get(name),
                      "status": p["write_status"]})
        if not isinstance(supplied, dict) or "value" not in supplied or not isinstance(supplied.get("source"), dict):
            entry["status"] = "explicit_value_units_and_source_required"
        else:
            entry.update({"new": supplied["value"], "unit": supplied.get("unit"), "source": supplied["source"]})
            source = supplied["source"]
            evidence = source_status(source)
            if evidence != "CONFIRMED":
                entry["status"] = evidence
            elif p["parameter_type"] in ("Text", "URL"):
                entry["status"] = "ready" if isinstance(entry["new"], string_types) else "text_required"
            elif p["parameter_type"] == "YesNo":
                entry["status"] = "ready" if isinstance(entry["new"], bool) else "boolean_required"
            elif p["parameter_type"] == "Integer":
                entry["status"] = "ready" if isinstance(entry["new"], int) and not isinstance(entry["new"], bool) else "integer_required"
            elif p["parameter_type"] == "Number":
                entry["status"] = "ready" if finite(entry["new"]) and entry["unit"] in (None, "number") else "dimensionless_number_required"
            else:
                valid_units = UNIT_TYPES.get(p["parameter_type"], {})
                entry["status"] = "ready" if finite(entry["new"]) and entry["unit"] in valid_units else "unsupported_type_or_explicit_unit_required"
            related = [m for m in mappings if m.get("target") == target]
            if not information and related and (len(related) != 1 or (related[0].get("confidence") != 1.0 and not related[0].get("confirmed"))):
                entry["status"] = "mapping_requires_confirmation"
            if related and related[0].get("guid") and related[0]["guid"].lower() != (p.get("guid") or "").lower():
                entry["status"] = "guid_mismatch"
        if p["write_status"] != "writable_type_parameter" and not allow_instance:
            entry["status"] = p["write_status"]
        if allow_instance:
            entry["allow_instance_default"] = True
            entry["write_scope"] = "family_instance_default_all_types"
            entry["affected_types"] = sorted(set(audit["types"] + [name]))
        if information:
            evidence, confidence = information_status(supplied if isinstance(supplied, dict) else {}, mappings, p)
            entry["confidence"] = confidence
            entry["classification"] = evidence
            if evidence == "SOURCE_CONFLICT":
                entry["conflicts"] = supplied["source"]["conflicts"]
            if entry["status"] == "ready" and evidence != "CONFIRMED":
                entry["status"] = evidence
            if entry["status"] == "ready":
                entry["action"] = "WRITE"
                information_write.append(entry)
                changes.append(entry)
            else:
                entry["action"] = "DO_NOT_WRITE"
                (review if evidence == "REVIEW" and entry["status"] == "REVIEW" else skipped).append(entry)
            continue
        if target in used_targets:
            entry["status"] = "duplicate_target"
        used_targets.add(target)
        if entry["status"] != "ready":
            errors.append(target + ": " + entry["status"])
        if entry["status"] == "ready":
            changes.append(entry)
    if any(e.get("status") == "unmapped" and not is_information_parameter({"name": e.get("parameter", "")}) for e in unmapped):
        errors.append("Some values are unmapped; no partial write will be performed")
    return {"family_type": name, "parameters_to_write": changes, "parameters_unmapped": unmapped,
            "parameter_mapping": mapping, "errors": errors, "ready": not errors,
            "adsk_parameters_to_write": information_write, "adsk_parameters_for_review": review,
            "adsk_parameters_skipped": skipped}
