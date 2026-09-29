# -*- coding: utf-8 -*-
"""Bounded, source-traceable static detailing of the existing Airhorse assembly."""
from core.active_plan import finite
from core.parameter_policy import source_status


def detail_plan(before, spec, generated):
    errors = []
    if before.get("ready") is False or before.get("is_read_only"):
        errors.append("Family preflight is not ready")
    items = spec.get("details", [])
    if not isinstance(items, list) or not 1 <= len(items) <= 250:
        errors.append("Expected 1..250 explicit detail primitives")
        items = []
    ids = [item.get("logical_id") for item in items]
    if any(not name or not name.startswith("airhorse/BPM-40A/detail/") for name in ids) or len(ids) != len(set(ids)):
        errors.append("Unique Airhorse detail IDs required")
    if set(ids).intersection(item["logical_id"] for item in generated):
        errors.append("Details already exist; automatic replacement is disabled")
    required = {"airhorse/BPM-40A/receiver_left", "airhorse/BPM-40A/receiver_right"}
    receivers = [g for g in generated if g["logical_id"] in required]
    if set(g["logical_id"] for g in receivers) != required:
        errors.append("Expected two original FamilyMCP Airhorse receivers")
    parameters = {p["name"]: p for p in before["parameters"]}
    for name, expected in {"L": 2250, "B": 1200, "H": 1740, "d1": 460, "l3": 1950}.items():
        p = parameters.get(name)
        if (not p or p["is_shared"] or p["is_instance"] or p["parameter_type"] != "Length"
                or p.get("formula") or any(v is None or abs(v * 304.8 - expected) > 0.001 for v in p["values_by_type_internal"].values())):
            errors.append("Unexpected baseline parameter: " + name)
    refinement = spec.get("receiver_refinement", {})
    if (refinement.get("parameter") != "l3" or refinement.get("straight_length_mm") != 1820
            or refinement.get("cap_depth_mm") != 65 or refinement.get("overall_length_mm") != 1950
            or source_status(refinement.get("source", {})) != "CONFIRMED"):
        errors.append("Explicit 1820 + 2*65 = 1950 mm receiver refinement required")
    for item in items:
        if source_status(item.get("source", {})) != "CONFIRMED" or item.get("approximate") is not True:
            errors.append("Every static detail needs source and approximate=true")
        kind = item.get("kind")
        if kind not in ("box", "panel", "cylinder", "dome"):
            errors.append("Unsupported detail primitive")
            continue
        center = item.get("center_mm", [])
        if len(center) != 3 or not all(finite(v) and abs(v) <= 3000 for v in center):
            errors.append("Invalid detail center")
        sizes = item.get("size_mm", []) if kind == "box" else ([item.get("width_mm"), item.get("height_mm"), item.get("depth_mm")] if kind == "panel" else [item.get("radius_mm"), item.get("depth_mm")])
        if not sizes or not all(finite(v) and 0.8 <= v <= 2500 for v in sizes):
            errors.append("Invalid detail dimensions")
        if kind != "box" and item.get("axis") not in ("X", "-X", "Y", "-Y", "Z", "-Z"):
            errors.append("Unsupported detail axis")
        if item.get("material") not in ("dark", "medium", "light"):
            errors.append("Expected neutral detail material")
        if item.get("grid"):
            columns, rows = item["grid"]
            if not all(type(v) is int and 1 <= v <= 20 for v in (columns, rows)):
                errors.append("Invalid perforation grid")
            if not finite(item.get("border_mm")) or not finite(item.get("web_mm")):
                errors.append("Explicit grille border and web required")
            elif any((length - 2 * item["border_mm"]) / count - item["web_mm"] < 2
                     for length, count in ((item["width_mm"], columns), (item["height_mm"], rows))):
                errors.append("Grille openings are too small")
        if kind == "dome" and (item["depth_mm"] != 65 or item["radius_mm"] != 230):
            errors.append("Unexpected receiver dome size")
    cap_centers = {tuple(item["center_mm"]): item.get("axis") for item in items if item.get("kind") == "dome"}
    expected_caps = {(x, y, 360): ("X" if x > 0 else "-X") for x in (-910, 910) for y in (-300, 300)}
    if cap_centers != expected_caps or sum(item.get("kind") == "dome" for item in items) != 4:
        errors.append("Four correctly positioned domes are required for receiver refinement")
    return {"success": True, "ready": not errors, "errors": errors,
            "warnings": ["Details are static, unbound and approximate; update them separately if base dimensions change."],
            "details": items, "receiver_refinement": refinement, "receivers_to_refine": receivers,
            "detail_count": len(items), "context_token": before["context_token"],
            "source_documents": spec.get("source_documents", []), "parameter_count_before": before["parameter_count"]}
