# -*- coding: utf-8 -*-
"""Read-only audit of the active family. IronPython 2.7; no Transactions."""
import hashlib
import json
import math

import clr
from pyrevit import DB
from revit.revit2021.unit_converter import UnitConverter


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def active_document(uiapp):
    require(str(uiapp.Application.VersionNumber) == "2021", "Revit 2021 required")
    require(str(clr.GetClrType(DB.Document).Assembly.GetName().Version) == "21.0.0.0", "Revit API 2021 required")
    require(uiapp.ActiveUIDocument is not None, "No active document; open the intended RFA manually")
    doc = uiapp.ActiveUIDocument.Document
    require(doc is not None and doc.IsFamilyDocument, "Active document is not a FamilyDocument")
    require(not doc.IsModifiable, "Active document already has an open transaction")
    require(doc.FamilyManager is not None, "FamilyManager is unavailable")
    return doc


def xyz(vector, mm=False):
    values = [float(vector.X), float(vector.Y), float(vector.Z)]
    return [float(UnitConverter.internal_to_mm(v)) for v in values] if mm else values


def collect(doc, cls):
    return list(DB.FilteredElementCollector(doc).OfClass(cls).WhereElementIsNotElementType())


def box_data(element):
    box = element.get_BoundingBox(None)
    if box is None:
        return None
    corners = [box.Transform.OfPoint(DB.XYZ(x, y, z)) for x in (box.Min.X, box.Max.X)
               for y in (box.Min.Y, box.Max.Y) for z in (box.Min.Z, box.Max.Z)]
    points = [xyz(point, True) for point in corners]
    lower = [min(point[i] for point in points) for i in range(3)]
    upper = [max(point[i] for point in points) for i in range(3)]
    return {"min_mm": lower, "max_mm": upper,
            "size_mm": [upper[i] - lower[i] for i in range(3)]}


def solid_count(geometry):
    total = 0
    if geometry is not None:
        for item in geometry:
            if isinstance(item, DB.Solid) and item.Volume > 1e-12:
                total += 1
            elif isinstance(item, DB.GeometryInstance):
                total += solid_count(item.GetInstanceGeometry())
    return total


def existing_geometry(doc):
    elements = []
    for cls in (DB.GenericForm, DB.FamilyInstance, DB.ImportInstance, DB.DirectShape):
        for element in collect(doc, cls):
            entry = {"element_id": element.Id.IntegerValue, "unique_id": element.UniqueId,
                     "kind": element.GetType().Name, "bbox": box_data(element)}
            options = DB.Options()
            options.IncludeNonVisibleObjects = True
            try:
                entry["solids"] = solid_count(element.get_Geometry(options))
            except Exception as error:
                entry["geometry_error"] = unicode(error)
                entry["solids"] = None
            elements.append(entry)
    return {"elements": sorted(elements, key=lambda e: e["element_id"]),
            "element_count": len(elements),
            "solid_count": sum(e["solids"] or 0 for e in elements),
            "has_existing_geometry": bool(elements)}


def raw_value(family_type, parameter):
    if family_type is None:
        return None
    storage = parameter.StorageType
    if storage == DB.StorageType.Double:
        value = family_type.AsDouble(parameter)
        return float(value) if value is not None else None
    if storage == DB.StorageType.Integer:
        value = family_type.AsInteger(parameter)
        return int(value) if value is not None else None
    if storage == DB.StorageType.String:
        return family_type.AsString(parameter)
    if storage == DB.StorageType.ElementId:
        value = family_type.AsElementId(parameter)
        return value.IntegerValue if value is not None else None
    return None


def parameters(doc):
    manager = doc.FamilyManager
    result = []
    types = sorted(list(manager.Types), key=lambda t: t.Name)
    for parameter in manager.Parameters:
        definition = parameter.Definition
        formula = parameter.Formula
        entry = {"id": parameter.Id.IntegerValue, "name": definition.Name,
                 "is_instance": bool(parameter.IsInstance), "is_shared": bool(parameter.IsShared),
                 "guid": str(parameter.GUID) if parameter.IsShared else None,
                 "parameter_type": str(definition.ParameterType),
                 "parameter_group": str(definition.ParameterGroup),
                 "storage_type": str(parameter.StorageType), "formula": formula,
                 "is_read_only": bool(parameter.IsReadOnly), "is_reporting": bool(parameter.IsReporting),
                 "is_adsk": "adsk" in definition.Name.lower() or u"адск" in definition.Name.lower(),
                 "current_value_internal": raw_value(manager.CurrentType, parameter),
                 "values_by_type_internal": {t.Name: raw_value(t, parameter) for t in types}}
        entry["write_status"] = ("controlled_by_formula" if formula else
                                  "read_only" if parameter.IsReadOnly or parameter.IsReporting else
                                  "instance_default_requires_opt_in" if parameter.IsInstance else
                                  "writable_type_parameter")
        entry["display_unit"] = None
        entry["current_value"] = entry["current_value_internal"]
        if parameter.StorageType == DB.StorageType.Double:
            try:
                unit = parameter.DisplayUnitType
                entry["display_unit"] = str(unit)
                if entry["current_value"] is not None and DB.UnitUtils.IsValidDisplayUnit(unit):
                    entry["current_value"] = float(DB.UnitUtils.ConvertFromInternalUnits(entry["current_value"], unit))
            except Exception:
                pass  # Raw internal value remains explicitly labelled; do not invent units.
        result.append(entry)
    return sorted(result, key=lambda p: (p["name"], p["id"]))


def connector_snapshot(doc):
    result = []
    for connector in collect(doc, DB.ConnectorElement):
        values = {}
        for parameter in connector.Parameters:
            storage = parameter.StorageType
            if storage == DB.StorageType.Double:
                value = parameter.AsDouble()
            elif storage == DB.StorageType.Integer:
                value = parameter.AsInteger()
            elif storage == DB.StorageType.String:
                value = parameter.AsString()
            elif storage == DB.StorageType.ElementId:
                value = parameter.AsElementId().IntegerValue
            else:
                value = None
            values[str(parameter.Id.IntegerValue)] = value
        transform = connector.CoordinateSystem
        result.append({"element_id": connector.Id.IntegerValue, "unique_id": connector.UniqueId,
                       "origin": xyz(connector.Origin), "direction": xyz(connector.Direction),
                       "basis_x": xyz(transform.BasisX), "basis_y": xyz(transform.BasisY),
                       "basis_z": xyz(transform.BasisZ), "parameters": values})
    return sorted(result, key=lambda c: c["element_id"])


def reference_planes(doc):
    result = []
    for plane in collect(doc, DB.ReferencePlane):
        defines = plane.get_Parameter(DB.BuiltInParameter.DATUM_PLANE_DEFINES_ORIGIN)
        reference = plane.get_Parameter(DB.BuiltInParameter.ELEM_IS_REFERENCE)
        geometric_plane = plane.GetPlane()
        result.append({"element_id": plane.Id.IntegerValue, "unique_id": plane.UniqueId,
                       "name": plane.Name, "normal": xyz(geometric_plane.Normal),
                       "origin_mm": xyz(geometric_plane.Origin, True),
                       "defines_origin": bool(defines and defines.AsInteger()),
                       "is_reference": reference.AsInteger() if reference else None})
    return sorted(result, key=lambda p: p["element_id"])


def coordinate_system(doc, planes):
    # Derive X/Y from geometric normals of origin planes, never from their names.
    coordinates = []
    for axis in (0, 1):
        matches = [p for p in planes if p["defines_origin"] and
                   abs(abs(p["normal"][axis]) - 1.0) < 1e-8]
        values = [p["origin_mm"][axis] for p in matches]
        if not values or max(values) - min(values) > 0.001:
            return {"ready": False, "reason": "No unambiguous axis-aligned origin planes"}
        coordinates.append(values[0])
    plans = [v for v in collect(doc, DB.ViewPlan) if not v.IsTemplate and v.GenLevel is not None
             and abs(abs(v.ViewDirection.Z) - 1.0) < 1e-8]
    levels = {v.GenLevel.Id.IntegerValue: v.GenLevel.Elevation for v in plans}
    if len(levels) != 1:
        return {"ready": False, "reason": "Expected one unambiguous reference level"}
    view = sorted(plans, key=lambda v: v.Id.IntegerValue)[0]
    coordinates.append(float(UnitConverter.internal_to_mm(view.GenLevel.Elevation)))
    return {"ready": True, "origin_mm": coordinates, "plan_view_id": view.Id.IntegerValue,
            "reference_level_id": view.GenLevel.Id.IntegerValue,
            "basis": "family XYZ; X/Y from DefinesOrigin planes; Z from reference level"}


def context(uiapp):
    doc = active_document(uiapp)
    manager = doc.FamilyManager
    category = doc.OwnerFamily.FamilyCategory
    return {"success": True, "document_title": doc.Title, "document_path": doc.PathName,
            "document_key": doc.OwnerFamily.UniqueId, "is_family_document": True,
            "is_modifiable": bool(doc.IsModifiable), "is_read_only": bool(doc.IsReadOnly),
            "is_modified": bool(doc.IsModified), "category": category.Name if category else None,
            "category_id": category.Id.IntegerValue if category else None,
            "family_name": doc.OwnerFamily.Name,
            "current_type": manager.CurrentType.Name if manager.CurrentType else None,
            "types": sorted(t.Name for t in manager.Types), "type_count": manager.Types.Size,
            "parameter_count": manager.Parameters.Size,
            "connector_count": len(collect(doc, DB.ConnectorElement))}


def inspect(uiapp):
    doc = active_document(uiapp)
    result = context(uiapp)
    result["parameters"] = parameters(doc)
    result["adsk_parameter_count"] = sum(p["is_adsk"] for p in result["parameters"])
    result["connectors"] = connector_snapshot(doc)
    result["existing_geometry"] = existing_geometry(doc)
    result["reference_planes"] = reference_planes(doc)
    result["coordinate_system"] = coordinate_system(doc, result["reference_planes"])
    # A stale audit cannot authorize a write to another document or altered family.
    fingerprint = dict(result)
    result["context_token"] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True,
                                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    return result


def preflight(uiapp):
    result = inspect(uiapp)
    result["warnings"] = []
    if result["existing_geometry"]["has_existing_geometry"]:
        result["warnings"].append("Existing geometry detected; preserved. Review overlap before adding bodies.")
    if result["connector_count"]:
        result["warnings"].append("Existing connector identities, positions and settings must remain unchanged.")
    result["ready"] = not result["is_read_only"] and result["coordinate_system"]["ready"]
    result["errors"] = [] if result["ready"] else ["Family is read-only or coordinate system is ambiguous"]
    return result
