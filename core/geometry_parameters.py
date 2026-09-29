# -*- coding: utf-8 -*-
"""Ordinary geometry parameters and bounded arithmetic formulas; no Revit imports."""
import copy
import re
from core.active_plan import finite, string_types
from core.parameter_policy import is_information_parameter, source_status

STANDARD_NAME = re.compile(u"^(?:L|B|H|D|d|[lbhdrtaxyzv][1-9][0-9]*|a_ang[1-9][0-9]*|α[1-9][0-9]*)$")
MANUFACTURER_NAME = re.compile(u"^[^\\W\\d]\\w{0,23}$", re.UNICODE)
TOKEN = re.compile(u"\\s*(?:(\\d+(?:\\.\\d*)?|\\.\\d+)|([^\\W\\d]\\w*)|([()+*/-]))", re.UNICODE)
USES = {"geometry", "position", "formula", "catalog", "flex", "visibility", "variant"}


def formula_tokens(expression):
    if not isinstance(expression, string_types) or not expression.strip() or len(expression) > 512:
        raise ValueError("A nonempty arithmetic formula of at most 512 characters is required")
    result, offset = [], 0
    expression = expression.strip()
    while offset < len(expression):
        match = TOKEN.match(expression, offset)
        if not match:
            raise ValueError("Unsupported formula syntax")
        result.append(("number", float(match.group(1))) if match.group(1) else
                      ("name", match.group(2)) if match.group(2) else ("operator", match.group(3)))
        offset = match.end()
    return result


def dependencies(expression):
    return set(value for kind, value in formula_tokens(expression) if kind == "name" and value not in ("mm", "deg"))


def evaluate_formula(expression, lookup):
    """Return (value, (length exponent, angle exponent)); never eval user text."""
    tokens, cursor = formula_tokens(expression), [0]
    def peek():
        return tokens[cursor[0]][1] if cursor[0] < len(tokens) else None
    def atom():
        if peek() in ("+", "-"):
            sign = -1 if peek() == "-" else 1
            cursor[0] += 1
            value, units = atom()
            return sign * value, units
        if peek() == "(":
            cursor[0] += 1
            value = add()
            if peek() != ")":
                raise ValueError("Unbalanced formula parentheses")
            cursor[0] += 1
            return value
        if cursor[0] >= len(tokens):
            raise ValueError("Incomplete formula")
        kind, value = tokens[cursor[0]]
        cursor[0] += 1
        if kind == "number":
            if peek() in ("mm", "deg"):
                unit = peek()
                cursor[0] += 1
                return value, (1, 0) if unit == "mm" else (0, 1)
            return value, (0, 0)
        if kind == "name":
            return lookup(value)
        raise ValueError("Invalid formula operand")
    def multiply():
        value, units = atom()
        while peek() in ("*", "/"):
            op = peek()
            cursor[0] += 1
            right, other = atom()
            if op == "/" and right == 0:
                raise ValueError("Division by zero in formula")
            value = value * right if op == "*" else value / right
            units = tuple(a + b if op == "*" else a - b for a, b in zip(units, other))
        return value, units
    def add():
        value, units = multiply()
        while peek() in ("+", "-"):
            op = peek()
            cursor[0] += 1
            right, other = multiply()
            if units != other:
                raise ValueError("Incompatible units in formula")
            value = value + right if op == "+" else value - right
        return value, units
    result = add()
    if cursor[0] != len(tokens) or not finite(result[0]):
        raise ValueError("Invalid or nonfinite formula result")
    return result


def geometry_parameter_plan(audit, declarations):
    virtual = copy.deepcopy(audit)
    created, reused, errors = [], [], []
    seen = set()
    for item in declarations:
        name = item.get("name", "")
        try:
            if not isinstance(name, string_types) or not name or name in seen:
                raise ValueError("Missing or duplicate geometry parameter name")
            seen.add(name)
            source = item.get("source", {})
            designation = source.get("designation")
            if is_information_parameter(item) or item.get("guid") or item.get("is_instance") or name.upper().startswith("MCP_"):
                raise ValueError("Geometry parameters must be ordinary TYPE parameters, never ADSK/shared/MCP")
            if designation:
                if name != designation:
                    raise ValueError("Preserve the manufacturer's designation: " + designation)
                if not MANUFACTURER_NAME.match(name):
                    raise ValueError("Manufacturer designation requires an explicit supported short identifier")
            elif not STANDARD_NAME.match(name):
                raise ValueError("Use engineering notation for a dimension without manufacturer designation")
            kind = item.get("type")
            if kind not in ("Length", "Angle", "YesNo"):
                raise ValueError("Geometry parameter type must be Length, Angle or YesNo")
            if not designation:
                expected_kind = "Angle" if name.startswith(u"α") or name.startswith("a_ang") else "YesNo" if re.match(r"^v[1-9][0-9]*$", name) else "Length"
                if kind != expected_kind:
                    raise ValueError("Engineering notation and parameter data type must agree")
            uses = item.get("used_for", [])
            if not item.get("purpose") or not uses or not set(uses).issubset(USES):
                raise ValueError("Purpose and explicit used_for roles are required")
            if kind == "YesNo" and not set(uses).intersection(("visibility", "variant")):
                raise ValueError("YesNo is only for visibility or geometry variants")
            status = source_status(source)
            if status != "CONFIRMED":
                raise ValueError("Geometry source: " + status)
            formula = item.get("formula")
            if formula:
                if str(source.get("source_type", "")).lower() != "derived" or source.get("calculation") != formula:
                    raise ValueError("Formula requires the exact derived calculation in source")
                dependencies(formula)
                if "value" in item or kind == "YesNo":
                    raise ValueError("Arithmetic formula cannot have an independent value or YesNo type")
            else:
                value, unit = item.get("value"), item.get("unit")
                valid = (isinstance(value, bool) if kind == "YesNo" else
                         finite(value) and unit == ("mm" if kind == "Length" else "deg"))
                if not valid:
                    raise ValueError("Explicit compatible geometry value and units are required")
            matches = [p for p in audit["parameters"] if p["name"] == name]
            entry = dict(item, is_shared=False, is_instance=False, naming="manufacturer" if designation else "engineering")
            if matches:
                if len(matches) != 1 or is_information_parameter(matches[0]) or matches[0].get("is_instance") or matches[0]["parameter_type"] != kind:
                    raise ValueError("Existing parameter cannot be replaced or retyped")
                if (matches[0].get("formula") or None) != (formula or None):
                    raise ValueError("Existing formula cannot be created, changed or removed automatically")
                if not formula and matches[0]["write_status"] != "writable_type_parameter":
                    raise ValueError("Existing parameter is protected")
                reused.append(entry)
            else:
                created.append(entry)
                virtual["parameters"].append({"name": name, "id": None, "parameter_type": kind,
                    "is_shared": False, "is_instance": False, "is_adsk": False, "guid": None,
                    "formula": formula, "write_status": "controlled_by_formula" if formula else "writable_type_parameter",
                    "values_by_type_internal": {}})
        except (ValueError, TypeError) as error:
            errors.append(name + ": " + str(error))
    definitions = {p["name"]: p for p in created + reused}
    order, visiting = [], set()
    def visit(name):
        if name in visiting:
            raise ValueError("Cyclic geometry formula: " + name)
        if name in order:
            return
        visiting.add(name)
        for dep in dependencies(definitions[name]["formula"]) if definitions[name].get("formula") else []:
            if dep not in definitions:
                raise ValueError("Formula dependency needs an explicit geometry declaration: " + dep)
            visit(dep)
        visiting.remove(name)
        order.append(name)
    try:
        for name in definitions:
            visit(name)
    except ValueError as error:
        errors.append(str(error))
    diameter_names = set(definitions).intersection(("D", "d"))
    numbered = any(re.match(r"^d[1-9][0-9]*$", n) for n in definitions)
    if (len(diameter_names) > 1 or (diameter_names and numbered)) and not all(p["naming"] == "manufacturer" for p in definitions.values()):
        errors.append("Use a consistent diameter notation: D/d for one diameter, d1... for several")
    return {"audit": virtual, "family_parameters_to_create": created, "family_parameters_reused": reused,
            "formula_order": [n for n in order if definitions[n].get("formula")], "errors": errors}


def resolve_dimensions(definitions, overrides):
    by_name = {p["name"]: p for p in definitions}
    resolved, visiting = {}, set()
    def lookup(name):
        if name in resolved:
            return resolved[name]
        if name in visiting or name not in by_name:
            raise ValueError("Cyclic or undeclared formula dependency: " + name)
        visiting.add(name)
        item = by_name[name]
        kind = item["type"]
        units = (1, 0) if kind == "Length" else (0, 1) if kind == "Angle" else (0, 0)
        if item.get("formula"):
            value, actual = evaluate_formula(item["formula"], lookup)
            if actual != units:
                raise ValueError("Formula result type mismatch: " + name)
        else:
            value = overrides.get(name, {}).get("value", item.get("value"))
        visiting.remove(name)
        resolved[name] = value, units
        return resolved[name]
    for name in by_name:
        lookup(name)
    return {name: value[0] for name, value in resolved.items()}
