# -*- coding: utf-8 -*-
"""Static native detailing, explicit receiver refinement, backup and preservation."""
import copy
import io
import json
import math
import os
import traceback
import uuid

from System import Double
from System.Collections.Generic import List
from pyrevit import DB
from core.detail_plan import detail_plan
from revit.revit2021 import active_family as audit
from revit.revit2021 import active_operations as ops
from revit.revit2021 import geometry_service as geo
from revit.revit2021.unit_converter import UnitConverter as Units


def point(values):
    return DB.XYZ(*[Units.mm_to_internal(v) for v in values])


def basis(axis):
    return {"X": (DB.XYZ.BasisY, DB.XYZ.BasisZ), "-X": (DB.XYZ.BasisY, -DB.XYZ.BasisZ),
            "Y": (DB.XYZ.BasisZ, DB.XYZ.BasisX), "-Y": (DB.XYZ.BasisX, DB.XYZ.BasisZ),
            "Z": (DB.XYZ.BasisX, DB.XYZ.BasisY), "-Z": (DB.XYZ.BasisX, -DB.XYZ.BasisY)}[axis]


def rectangle(center, u, v, width, height, reverse=False):
    w, h = Units.mm_to_internal(width / 2.0), Units.mm_to_internal(height / 2.0)
    vertices = [center - u * w - v * h, center + u * w - v * h,
                center + u * w + v * h, center - u * w + v * h]
    if reverse:
        vertices.reverse()
    result = DB.CurveArray()
    for index in range(4):
        result.Append(DB.Line.CreateBound(vertices[index], vertices[(index + 1) % 4]))
    return result


def native_form(doc, item):
    center = point(item["center_mm"])
    profiles = DB.CurveArrArray()
    kind = item["kind"]
    if kind == "box":
        width, height, depth = item["size_mm"]
        u, v = basis("Z")
        center -= DB.XYZ.BasisZ * Units.mm_to_internal(depth / 2.0)
        profiles.Append(rectangle(center, u, v, width, height))
    else:
        u, v = basis(item["axis"])
        depth = item["depth_mm"]
        if kind == "cylinder":
            curves = DB.CurveArray()
            radius = Units.mm_to_internal(item["radius_mm"])
            curves.Append(DB.Arc.Create(center, radius, 0, math.pi, u, v))
            curves.Append(DB.Arc.Create(center, radius, math.pi, 2 * math.pi, u, v))
            profiles.Append(curves)
        else:
            width, height = item["width_mm"], item["height_mm"]
            profiles.Append(rectangle(center, u, v, width, height))
            if item.get("grid"):
                columns, rows = item["grid"]
                border, web = item["border_mm"], item["web_mm"]
                dx, dy = float(width - 2 * border) / columns, float(height - 2 * border) / rows
                for column in range(columns):
                    for row in range(rows):
                        opening = center + u * Units.mm_to_internal(-width / 2.0 + border + (column + .5) * dx)
                        opening += v * Units.mm_to_internal(-height / 2.0 + border + (row + .5) * dy)
                        profiles.Append(rectangle(opening, u, v, dx - web, dy - web, True))
    normal = u.CrossProduct(v)
    plane = DB.SketchPlane.Create(doc, DB.Plane.CreateByNormalAndOrigin(normal, center))
    form = doc.FamilyCreate.NewExtrusion(True, profiles, plane, Units.mm_to_internal(depth))
    return form, plane


def dome(doc, item):
    center = point(item["center_mm"])
    u, v = basis(item["axis"])
    axis = u.CrossProduct(v)
    r, h = Units.mm_to_internal(item["radius_mm"]), Units.mm_to_internal(item["depth_mm"])
    sphere_radius = (r * r + h * h) / (2 * h)
    sphere_center = center + axis * (h - sphere_radius)
    angle = math.acos((sphere_radius - h) / sphere_radius)
    outer, tip = center + u * r, center + axis * h
    midpoint = sphere_center + axis * (sphere_radius * math.cos(angle / 2)) + u * (sphere_radius * math.sin(angle / 2))
    loop = DB.CurveLoop()
    loop.Append(DB.Line.CreateBound(center, outer))
    loop.Append(DB.Arc.Create(outer, tip, midpoint))
    loop.Append(DB.Line.CreateBound(tip, center))
    frame = DB.Frame(center, u, axis.CrossProduct(u), axis)
    solid = DB.GeometryCreationUtilities.CreateRevolvedGeometry(frame, List[DB.CurveLoop]([loop]), 0, 2 * math.pi)
    expected_volume = math.pi * h * (3 * r * r + h * h) / 6
    audit.require(abs(solid.Volume / expected_volume - 1) < .001, "Dome volume differs from spherical cap")
    return DB.FreeFormElement.Create(doc, solid), None


def create_view(doc):
    view_type = next(t for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType) if t.ViewFamily == DB.ViewFamily.ThreeDimensional)
    view = DB.View3D.CreateIsometric(doc, view_type.Id)
    view.Name = "Airhorse - details"
    view.DetailLevel = DB.ViewDetailLevel.Fine
    view.Scale = 20
    view.DisplayStyle = DB.DisplayStyle.ShadingWithEdges
    view.IsSectionBoxActive = False
    view.CropBoxActive = False
    view.CropBoxVisible = False
    forward = DB.XYZ(-1, 1, -.75).Normalize()
    right = forward.CrossProduct(DB.XYZ.BasisZ).Normalize()
    up = right.CrossProduct(forward).Normalize()
    eye = point([0, 0, 850]) - forward * 20
    view.SetOrientation(DB.ViewOrientation3D(eye, up, forward))
    return view


def dispatch(uiapp, data, root):
    if data.get('spec', {}).get('profile') == 'wos8_drawing_3400401':
        from revit.revit2021 import wos_details
        reload(wos_details)
        return wos_details.dispatch(uiapp, data, root)
    before = audit.preflight(uiapp)
    doc = audit.active_document(uiapp)
    spec = data.get("spec", {})
    result = detail_plan(before, spec, geo.generated(doc))
    result.update(dry_run=data.get("dry_run", True), preflight=before, saved=False, model_committed=False,
                  transaction_group_rolled_back=False, created_details=[], parameter_updates=[])
    audit.require(type(result["dry_run"]) is bool, "dry_run must be boolean")
    if result["dry_run"]:
        return result
    audit.require(data.get("context_token") == before["context_token"], "Stale detail context token")
    if not result["ready"]:
        result["success"] = False
        return result
    for source in result["source_documents"]:
        audit.require(ops.hash_file(source["file"]) == source["sha256"], "Source drawing changed")
    run_id = str(uuid.uuid4())
    output = os.path.join(root, "tests", "output", run_id)
    backup = os.path.join(root, "backup", run_id)
    os.makedirs(output)
    os.makedirs(backup)
    original_path, original_hash = doc.PathName, ops.hash_file(doc.PathName)
    result.update(run_id=run_id, backup_path=os.path.join(backup, "Airhorse_before_details.rfa"),
                  output_path=os.path.join(output, "Airhorse_BPM-40A_Detailed.rfa"), errors=[])
    group = None
    try:
        result["stage"] = "backup"
        ops.save_as(doc, result["backup_path"])
        group = DB.TransactionGroup(doc, "Airhorse PDF details")
        audit.require(group.Start() == DB.TransactionStatus.Started, "Could not start detail group")
        allowed = set()
        result["stage"] = "receiver_refinement"
        def refine():
            manager = doc.FamilyManager
            for family_type in manager.Types:
                manager.CurrentType = family_type
                p = ops.find_parameter(doc, "l3")
                manager.Set(p, Double(Units.mm_to_internal(1820)))
                allowed.add((family_type.Name, "l3"))
                result["parameter_updates"].append({"parameter": "l3", "family_type": family_type.Name,
                    "old_mm": 1950, "new_mm": 1820, "source": spec["receiver_refinement"]["source"]})
            manager.CurrentType = next(t for t in manager.Types if t.Name == before["current_type"])
            doc.Regenerate()
            for receiver in result["receivers_to_refine"]:
                element = doc.GetElement(DB.ElementId(receiver["element_id"]))
                y = -300 if receiver["logical_id"].endswith("left") else 300
                measured = geo.measure_box(element, {"origin_mm": [0, y, 360]}, [1820, 460, 460], True)
                result.setdefault("receiver_validation", []).append(dict(measured, logical_id=receiver["logical_id"], unique_id=element.UniqueId))
        ops.transaction(doc, "Refine straight receiver sections", refine, result)
        result["stage"] = "static_details"
        def build():
            materials = {}
            for key, shade in (("dark", 45), ("medium", 140), ("light", 205)):
                name = "Airhorse detail " + key
                found = [m for m in audit.collect(doc, DB.Material) if m.Name == name]
                material = found[0] if found else doc.GetElement(DB.Material.Create(doc, name))
                if not found:
                    material.Color = DB.Color(shade, shade, shade)
                materials[key] = material.Id
            for item in result["details"]:
                try:
                    element, auxiliary = dome(doc, item) if item["kind"] == "dome" else native_form(doc, item)
                    parameter = element.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
                    audit.require(parameter is not None and not parameter.IsReadOnly, "Detail material parameter unavailable")
                    parameter.Set(materials[item["material"]])
                    if item.get("fine_only", True):
                        visibility = DB.FamilyElementVisibility(DB.FamilyElementVisibilityType.Model)
                        visibility.IsShownInFine = True
                        visibility.IsShownInMedium = False
                        visibility.IsShownInCoarse = False
                        element.SetVisibility(visibility)
                    geo.tag(element, item["logical_id"], run_id)
                    if auxiliary:
                        geo.tag(auxiliary, item["logical_id"] + "/plane", run_id)
                    result["created_details"].append({"element_id": element.Id.IntegerValue,
                        "unique_id": element.UniqueId, "logical_id": item["logical_id"], "kind": item["kind"]})
                except Exception as error:
                    raise RuntimeError(item["logical_id"] + ": " + unicode(error))
            doc.Regenerate()
        ops.transaction(doc, "Add source-based static details", build, result)
        result["stage"] = "validation"
        retained = copy.deepcopy(before)
        refined_ids = set(r["unique_id"] for r in result["receivers_to_refine"])
        retained["existing_geometry"]["elements"] = [e for e in retained["existing_geometry"]["elements"] if e["unique_id"] not in refined_ids]
        result["preservation"] = ops.preserved(doc, retained, allowed)
        audit.require(doc.FamilyManager.Parameters.Size == before["parameter_count"], "Detailing added a family parameter")
        after = audit.existing_geometry(doc)
        audit.require(after["element_count"] == before["existing_geometry"]["element_count"] + len(result["details"]), "Unexpected form count")
        audit.require(after["solid_count"] == after["element_count"], "Each form must contain one solid")
        lower = [min(e["bbox"]["min_mm"][axis] for e in after["elements"]) for axis in range(3)]
        upper = [max(e["bbox"]["max_mm"][axis] for e in after["elements"]) for axis in range(3)]
        audit.require(all(lower[i] >= [-1125, -600, 0][i] - .01 and upper[i] <= [1125, 600, 1741][i] + .01 for i in range(3)), "Detailing exceeded declared equipment envelope")
        result["assembly_validation"] = {"success": True, "min_mm": lower, "max_mm": upper,
            "size_mm": [upper[i] - lower[i] for i in range(3)], "solids": after["solid_count"]}
        result["stage"] = "presentation_view"
        view = ops.transaction(doc, "Create detail presentation view", lambda: create_view(doc), result)
        audit.require(group.Assimilate() == DB.TransactionStatus.Committed, "Detail group did not commit")
        group.Dispose()
        group = None
        result["model_committed"] = True
        result["stage"] = "save"
        uiapp.ActiveUIDocument.ActiveView = view
        for ui_view in uiapp.ActiveUIDocument.GetOpenUIViews():
            if ui_view.ViewId == view.Id:
                ui_view.ZoomToFit()
        ops.save_as(doc, result["output_path"])
        result["saved"] = True
        options = DB.ImageExportOptions()
        try:
            options.FilePath = os.path.join(output, "details")
            options.ExportRange = DB.ExportRange.SetOfViews
            options.SetViewsAndSheets(List[DB.ElementId]([view.Id]))
            options.ZoomType = DB.ZoomFitType.FitToPage
            options.PixelSize = 2000
            options.HLRandWFViewsFileType = DB.ImageFileType.PNG
            options.ShadowViewsFileType = DB.ImageFileType.PNG
            doc.ExportImage(options)
            result["preview"] = {"paths": [os.path.join(output, f) for f in os.listdir(output) if f.endswith(".png")]}
        except Exception as error:
            result["preview"] = {"error": unicode(error)}
        finally:
            options.Dispose()
        result["original_file_unchanged"] = ops.hash_file(original_path) == original_hash
        audit.require(result["original_file_unchanged"], "Original RFA changed on disk")
        result["success"] = True
        result["stage"] = "complete"
    except Exception as error:
        result.update(success=False, error=unicode(error), traceback=traceback.format_exc())
        result["errors"].append(unicode(error))
    finally:
        if group is not None:
            if group.GetStatus() == DB.TransactionStatus.Started:
                result["transaction_group_rolled_back"] = group.RollBack() == DB.TransactionStatus.RolledBack
            group.Dispose()
        result["active_rfa_path_after"] = doc.PathName
        result["log_path"] = os.path.join(output, "detail-result.json")
        with io.open(result["log_path"], "w", encoding="utf-8") as stream:
            stream.write(unicode(json.dumps(result, ensure_ascii=False, indent=2)))
    return result


def presentation(uiapp, data, root):
    """Fix only the active 3D presentation; verify model and parameters unchanged."""
    before = audit.inspect(uiapp)
    doc = audit.active_document(uiapp)
    view = doc.ActiveView
    audit.require(isinstance(view, DB.View3D) and view.Name == "Airhorse - details", "Activate the generated detail view")
    result = {"success": True, "ready": True, "dry_run": data.get("dry_run", True), "context_token": before["context_token"],
              "errors": [], "warnings": [], "saved": False, "model_committed": False, "transaction_group_rolled_back": False}
    if result["dry_run"]:
        return result
    audit.require(data.get("context_token") == before["context_token"], "Stale presentation context")
    run_id = str(uuid.uuid4())
    backup = os.path.join(root, "backup", run_id)
    output = os.path.join(root, "tests", "output", run_id)
    os.makedirs(backup)
    os.makedirs(output)
    result["backup_path"] = os.path.join(backup, "Airhorse_before_presentation.rfa")
    result["output_path"] = os.path.join(output, "Airhorse_BPM-40A_Detailed.rfa")
    original_path, original_hash = doc.PathName, ops.hash_file(doc.PathName)
    ops.save_as(doc, result["backup_path"])
    def style():
        view.Scale = 20
        settings = DB.OverrideGraphicSettings()
        settings.SetProjectionLineWeight(1)
        settings.SetProjectionLineColor(DB.Color(65, 65, 65))
        view.SetCategoryOverrides(doc.OwnerFamily.FamilyCategory.Id, settings)
    ops.transaction(doc, "Detail preview line weights", style, result)
    result["model_committed"] = True
    result["validation"] = ops.preserved(doc, before, set())
    ops.save_as(doc, result["output_path"])
    result["saved"] = True
    options = DB.ImageExportOptions()
    try:
        options.FilePath = os.path.join(output, "details")
        options.ExportRange = DB.ExportRange.SetOfViews
        options.SetViewsAndSheets(List[DB.ElementId]([view.Id]))
        options.ZoomType = DB.ZoomFitType.FitToPage
        options.PixelSize = 2000
        options.HLRandWFViewsFileType = DB.ImageFileType.PNG
        options.ShadowViewsFileType = DB.ImageFileType.PNG
        doc.ExportImage(options)
        result["preview"] = {"paths": [os.path.join(output, f) for f in os.listdir(output) if f.endswith(".png")]}
    finally:
        options.Dispose()
    result["original_file_unchanged"] = ops.hash_file(original_path) == original_hash
    audit.require(result["original_file_unchanged"], "Original RFA changed")
    result["active_rfa_path_after"] = doc.PathName
    return result
