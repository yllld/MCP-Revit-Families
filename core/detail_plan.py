# -*- coding: utf-8 -*-
"""Product-independent declarative geometry contract. Python 2.7 and 3."""
from __future__ import division
import re
from core.active_plan import finite
from core.parameter_policy import source_status

try:
    TEXT = (basestring,)
except NameError:
    TEXT = (str,)


def check(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, low=-100000, high=100000):
    check(finite(value) and low <= value <= high, "Invalid finite number: " + str(value))
    return value


def vector(value, count=3):
    check(isinstance(value, list) and len(value) == count, "Expected coordinate vector")
    return [number(x) for x in value]


def keys(value, allowed, required=()):
    check(isinstance(value, dict), "Expected object")
    check(not set(value).difference(allowed), "Unsupported fields: " + str(sorted(set(value).difference(allowed))))
    check(not set(required).difference(value), "Missing fields: " + str(sorted(set(required).difference(value))))


def frame(value):
    keys(value, ("origin_mm", "u", "v"), ("origin_mm", "u", "v"))
    vector(value["origin_mm"])
    u, v = vector(value["u"]), vector(value["v"])
    check(abs(sum(x*x for x in u) - 1) < 1e-6 and abs(sum(x*x for x in v) - 1) < 1e-6, "Frame u and v must be unit vectors")
    check(abs(sum(x*y for x, y in zip(u, v))) < 1e-6, "Frame u and v must be perpendicular")


def profile(value):
    check(isinstance(value, dict), "Expected profile object")
    kind = value.get("kind")
    if kind in ("circle", "ellipse", "rectangle", "rounded_rectangle"):
        fields = {"circle": ("radius_mm",), "ellipse": ("rx_mm", "ry_mm"),
                  "rectangle": ("width_mm", "height_mm"),
                  "rounded_rectangle": ("width_mm", "height_mm", "radius_mm")}[kind]
        keys(value, ("kind", "offset_mm") + fields, ("kind",) + fields)
        for name in fields:
            number(value[name], .5)
        vector(value.get("offset_mm", [0, 0]), 2)
        if kind == "rounded_rectangle":
            check(2*value["radius_mm"] < min(value["width_mm"], value["height_mm"]), "Corner diameter must be smaller than both rectangle sides")
    elif kind == "polygon":
        keys(value, ("kind", "points_mm"), ("points_mm",))
        points = value["points_mm"]
        check(isinstance(points, list) and 3 <= len(points) <= 256, "Expected 3..256 polygon vertices")
        for p in points:
            vector(p, 2)
        for a, b in zip(points, points[1:] + points[:1]):
            check(sum((x-y)**2 for x, y in zip(a, b)) >= .25, "Profile edge shorter than 0.5 mm")
        area = sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(points, points[1:]+points[:1]))
        check(abs(area) > .5, "Degenerate polygon")
    elif kind == "curves":
        keys(value, ("kind", "segments"), ("segments",))
        segments = value["segments"]
        check(isinstance(segments, list) and 2 <= len(segments) <= 256, "Expected 2..256 line/arc segments")
        for segment in segments:
            check(isinstance(segment, dict), "Expected segment object")
            check(segment.get("kind") in ("line", "arc"), "Only line and three-point arc segments are supported")
            fields = ("kind", "start_mm", "end_mm") + (("mid_mm",) if segment["kind"] == "arc" else ())
            keys(segment, fields, fields)
            a, b = vector(segment["start_mm"], 2), vector(segment["end_mm"], 2)
            check(sum((x-y)**2 for x, y in zip(a, b)) >= .25, "Segment shorter than 0.5 mm")
            if segment["kind"] == "arc":
                m = vector(segment["mid_mm"], 2)
                check(abs((b[0]-a[0])*(m[1]-a[1])-(b[1]-a[1])*(m[0]-a[0])) > 1e-6, "Arc points must not be collinear")
        for a, b in zip(segments, segments[1:]+segments[:1]):
            check(all(abs(x-y) < 1e-6 for x, y in zip(a["end_mm"], b["start_mm"])), "Profile is not closed")
    else:
        raise ValueError("Unsupported profile: " + str(kind))


def geometry(value, budget, level=0):
    check(isinstance(value, dict), "Expected geometry object")
    budget[0] += 1
    check(level <= 6 and budget[0] <= 1000, "Geometry complexity limit exceeded")
    kind = value.get("kind")
    if kind == "boolean":
        keys(value, ("kind", "operation", "left", "right"), ("operation", "left", "right"))
        check(value["operation"] in ("union", "difference", "intersection"), "Unsupported boolean operation")
        geometry(value["left"], budget, level+1)
        geometry(value["right"], budget, level+1)
        return
    check(kind in ("extrusion", "revolve", "loft"), "Unsupported geometry: " + str(kind))
    fields = {"extrusion": ("loops", "depth_mm"), "revolve": ("profile", "angle_deg"), "loft": ("sections",)}[kind]
    keys(value, ("kind", "frame") + fields, ("kind", "frame") + fields)
    frame(value["frame"])
    if kind == "extrusion":
        number(value["depth_mm"], .5)
        check(isinstance(value["loops"], list) and 1 <= len(value["loops"]) <= 100, "Expected 1..100 profile loops")
        for loop in value["loops"]:
            profile(loop)
    elif kind == "revolve":
        number(value["angle_deg"], .1, 360)
        profile(value["profile"])
    else:
        sections = value["sections"]
        check(isinstance(sections, list) and 2 <= len(sections) <= 32, "Expected 2..32 loft sections")
        last = None
        for section in sections:
            keys(section, ("offset_mm", "profile"), ("offset_mm", "profile"))
            number(section["offset_mm"])
            check(last is None or section["offset_mm"] - last >= .5, "Loft sections must increase by at least 0.5 mm")
            profile(section["profile"])
            last = section["offset_mm"]


def bounds(value):
    keys(value, ("min_mm", "max_mm"), ("min_mm", "max_mm"))
    lo, hi = vector(value["min_mm"]), vector(value["max_mm"])
    check(all(b > a for a, b in zip(lo, hi)), "Expected nonzero bounding box")


def compare_bounds(actual, expected, tolerance):
    """Measure all six bounds, not merely overall size or containment."""
    deltas = {key: [a-e for a, e in zip(actual[key], expected[key])] for key in ("min_mm", "max_mm")}
    return {"success": all(abs(x) <= tolerance for values in deltas.values() for x in values),
            "actual": actual, "expected": expected, "delta_mm": deltas, "tolerance_mm": tolerance}


def detail_plan(before, spec, generated):
    errors, warnings, items, replacement, requirements = [], [], [], [], []
    def attempt(label, action):
        try:
            action()
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            errors.append(label + ": " + str(error))
    if before.get("ready") is False or before.get("is_read_only"):
        errors.append("Family preflight is not ready")
    try:
        keys(spec, ("schema_version", "details", "source_documents", "replace_generated_ids", "expected_bounds_mm",
                    "tolerance_mm", "requirements", "accept_scaled_dimensions", "accept_assumptions"),
             ("schema_version", "details", "expected_bounds_mm", "tolerance_mm", "requirements"))
        check(type(spec["schema_version"]) is int and spec["schema_version"] == 1, "Expected universal detail schema_version=1; legacy product profiles are not supported")
        check(isinstance(spec["details"], list) and 1 <= len(spec["details"]) <= 250, "Expected 1..250 details")
        items = spec["details"]
        for field in ("accept_scaled_dimensions", "accept_assumptions"):
            check(type(spec.get(field, False)) is bool, field + " must be boolean")
    except (ValueError, TypeError, KeyError) as error:
        errors.append(str(error))
        spec, items = {}, []
    attempt("expected_bounds_mm", lambda: bounds(spec.get("expected_bounds_mm")))
    attempt("tolerance_mm", lambda: number(spec.get("tolerance_mm"), .01, 100))
    budget, ids = [0], []
    for index, item in enumerate(items):
        def validate_item():
            keys(item, ("logical_id", "geometry", "source", "accuracy", "material_rgb", "fine_only", "expected_bounds_mm"),
                 ("logical_id", "geometry", "source", "accuracy"))
            name = item["logical_id"]
            check(isinstance(name, TEXT) and re.match(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,119}$", name), "Invalid logical_id")
            check(not name.endswith("/plane") and not name.startswith("presentation/"), "Reserved logical_id")
            check(name not in ids, "Duplicate logical_id")
            ids.append(name)
            check(source_status(item["source"]) == "CONFIRMED", "Detail source is not confirmed")
            source = item["source"]
            check(source.get("file") or source.get("reference"), "Detail source reference is required")
            accuracy = item["accuracy"]
            check(accuracy in ("dimensioned", "scaled", "assumed"), "Invalid accuracy")
            if accuracy == "scaled":
                check(spec.get("accept_scaled_dimensions") is True, "Scaled dimensions require explicit acceptance")
                check(source.get("source_type") == "derived", "Scaled geometry requires a derived source")
                calibration = source.get("calibration", {})
                check(isinstance(calibration, dict), "Expected scale calibration object")
                for key in ("known_mm", "known_drawing_units", "measured_drawing_units", "result_mm", "uncertainty_mm"):
                    number(calibration.get(key), .000001)
                calculated = calibration["known_mm"] * calibration["measured_drawing_units"] / calibration["known_drawing_units"]
                check(abs(calculated-calibration["result_mm"]) <= .001, "Scale calibration arithmetic mismatch")
                check(source.get("view") and source.get("page"), "Scaled source requires page and orthographic view")
                warnings.append(name + ": scaled dimensions; uncertainty is not a manufacturing tolerance")
            if accuracy == "assumed":
                check(spec.get("accept_assumptions") is True, "Assumed geometry requires explicit acceptance")
                check(source.get("reason"), "Assumption reason is required")
                warnings.append(name + ": assumed geometry, not verified against drawing")
            if accuracy == "dimensioned":
                check(source.get("source_type") in ("direct", "user_input", "integration_test"), "Dimensioned geometry requires direct dimensions")
            rgb = item.get("material_rgb", [180, 180, 180])
            check(isinstance(rgb, list) and len(rgb) == 3 and all(type(x) is int and 0 <= x <= 255 for x in rgb), "Invalid material_rgb")
            check(type(item.get("fine_only", False)) is bool, "fine_only must be boolean")
            if "expected_bounds_mm" in item:
                bounds(item["expected_bounds_mm"])
            geometry(item["geometry"], budget)
        attempt("detail " + str(index), validate_item)
    requested = spec.get("replace_generated_ids", [])
    def replacements():
        check(isinstance(requested, list) and all(isinstance(n, TEXT) for n in requested), "Expected replacement ID list")
        check(len(requested) == len(set(requested)), "Duplicate replacement ID")
        original_ids = set(e["unique_id"] for e in before.get("existing_geometry", {}).get("elements", []))
        for name in requested:
            matches = [g for g in generated if g.get("logical_id") == name and g.get("unique_id") in original_ids]
            check(len(matches) == 1, "Replacement requires one existing tagged form: " + name)
            replacement.extend(matches)
        conflicts = set(ids).intersection(g.get("logical_id") for g in generated).difference(requested)
        check(not conflicts, "Details already exist; explicitly select replacement IDs: " + str(sorted(conflicts)))
    attempt("replacement", replacements)
    def coverage():
        rows = spec.get("requirements")
        check(isinstance(rows, list) and 1 <= len(rows) <= 250, "Expected explicit requirements coverage")
        seen, covered = set(), set()
        for row in rows:
            keys(row, ("id", "description", "detail_ids"), ("id", "description", "detail_ids"))
            check(isinstance(row["id"], TEXT) and row["id"] and row["id"] not in seen, "Unique requirement ID required")
            check(isinstance(row["description"], TEXT) and row["description"].strip(), "Requirement description required")
            check(isinstance(row["detail_ids"], list) and row["detail_ids"] and all(isinstance(n, TEXT) for n in row["detail_ids"]), "Every requirement must reference modeled details")
            check(set(row["detail_ids"]).issubset(ids), "Requirement refers to a missing detail")
            seen.add(row["id"])
            covered.update(row["detail_ids"])
            requirements.append(row)
        check(covered == set(ids), "Every detail must be mapped to a requirement")
    attempt("requirements", coverage)
    documents = spec.get("source_documents", [])
    def sources():
        check(isinstance(documents, list) and len(documents) <= 50, "Invalid source_documents")
        for source in documents:
            keys(source, ("file", "sha256"), ("file", "sha256"))
            check(isinstance(source["file"], TEXT) and source["file"], "Source file required")
            check(isinstance(source["sha256"], TEXT) and re.match(r"^[0-9a-f]{64}$", source["sha256"]), "Invalid source SHA256")
        declared = set(source["file"] for source in documents)
        for item in items:
            source = item.get("source", {}) if isinstance(item, dict) else {}
            if isinstance(source, dict) and source.get("file"):
                check(source["file"] in declared, "Every local source file requires a source_documents SHA256")
    attempt("source_documents", sources)
    warnings.append("Detail geometry is static in all family types; changing Family Parameters does not rebuild it. No ADSK or connector edits.")
    warnings.append("Coverage and bounds validate the supplied specification, not completeness or visual fidelity of the drawing. Compare exported views with the source.")
    return {"success": True, "ready": not errors, "errors": errors, "warnings": warnings,
            "schema_version": 1, "details": items, "detail_count": len(items), "replacement_elements": replacement,
            "requirements": requirements, "context_token": before.get("context_token"),
            "source_documents": documents, "parameter_count_before": before.get("parameter_count"),
            "expected_bounds_mm": spec.get("expected_bounds_mm"), "tolerance_mm": spec.get("tolerance_mm"),
            "geometry_node_count": budget[0], "visual_review_required": True, "parametric_details": False}
