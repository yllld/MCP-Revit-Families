# -*- coding: utf-8 -*-
"""Parameter provenance rules, independent of Revit (Python 2.7/3)."""
import math

POLICY_VERSION = "2"
CONFIRMED_THRESHOLD = 0.95
REVIEW_THRESHOLD = 0.70


def is_information_parameter(parameter):
    name = parameter.get("name", "").lower()
    return bool(parameter.get("is_shared") or parameter.get("is_adsk") or
                "adsk" in name or u"адск" in name)


def source_status(source, information=False):
    if not isinstance(source, dict):
        return "UNKNOWN"
    if source.get("conflicts"):
        return "SOURCE_CONFLICT"
    kind = str(source.get("source_type", "")).lower()
    if kind not in ("direct", "derived", "user_input", "integration_test"):
        return "UNKNOWN"
    if not (source.get("field") or source.get("designation")):
        return "UNKNOWN"
    if source.get("origin") == "external" and source.get("search_authorized") is not True:
        return "EXTERNAL_SOURCE_NOT_AUTHORIZED"
    if information:
        if kind != "direct":
            return "DIRECT_SOURCE_REQUIRED"
        if source.get("origin") not in ("user_provided", "external"):
            return "SOURCE_ORIGIN_REQUIRED"
        if not (source.get("file") or source.get("reference")):
            return "SOURCE_REFERENCE_REQUIRED"
        if source.get("model_match") is not True:
            return "MODEL_NOT_CONFIRMED"
    elif kind == "derived" and not source.get("calculation"):
        return "DERIVATION_REQUIRED"
    return "CONFIRMED"


def information_status(supplied, mappings, parameter):
    """No confidence, confirmed flag or exact name bypasses source validation."""
    related = [m for m in mappings if m.get("target") == parameter["name"]]
    if isinstance(supplied.get("source"), dict) and supplied["source"].get("conflicts"):
        return "SOURCE_CONFLICT", None
    if len(related) > 1:
        return "AMBIGUOUS_MAPPING", None
    scores = [x for x in (supplied.get("confidence"), related[0].get("confidence") if related else None)
              if x is not None]
    if not scores or any(isinstance(x, bool) or not isinstance(x, (int, float)) or
                         math.isnan(x) or math.isinf(x) or not 0 <= x <= 1 for x in scores):
        return "UNKNOWN", None
    confidence = min(scores)  # Conflicting declarations never raise confidence.
    status = source_status(supplied.get("source"), information=True)
    if status != "CONFIRMED":
        return status, confidence
    if related and related[0].get("guid") and related[0]["guid"].lower() != (parameter.get("guid") or "").lower():
        return "GUID_MISMATCH", confidence
    return ("CONFIRMED" if confidence >= CONFIRMED_THRESHOLD else
            "REVIEW" if confidence >= REVIEW_THRESHOLD else "UNKNOWN"), confidence
