# -*- coding: utf-8 -*-
"""Universal source-based detailing with atomic validation and separate saves."""
from __future__ import division
import copy
import io
import json
import os
import traceback
import uuid
from System.Collections.Generic import List
from pyrevit import DB
from core.detail_plan import detail_plan, compare_bounds
from revit.revit2021 import active_family as audit
from revit.revit2021 import active_operations as ops
from revit.revit2021 import geometry_service as geo
from revit.revit2021 import detail_geometry as shapes


def create_views(doc, run_id, bounds):
    view_type = next(t for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
                     if t.ViewFamily == DB.ViewFamily.ThreeDimensional)
    center = shapes.point([(a+b)/2 for a, b in zip(bounds["min_mm"], bounds["max_mm"])])
    views = []
    for name, direction, vertical in (("3D", [-1, 1, -.75], [0, 0, 1]),
                                      ("Front", [0, 1, 0], [0, 0, 1]),
                                      ("Right", [-1, 0, 0], [0, 0, 1]),
                                      ("Top", [0, 0, -1], [0, 1, 0])):
        view = DB.View3D.CreateIsometric(doc, view_type.Id)
        view.Name = "MCP Details " + name + " " + run_id[:8]
        view.DetailLevel, view.Scale = DB.ViewDetailLevel.Fine, 20
        view.DisplayStyle = DB.DisplayStyle.ShadingWithEdges if name == "3D" else DB.DisplayStyle.HLR
        view.IsSectionBoxActive = False
        view.CropBoxActive = False
        view.CropBoxVisible = False
        forward = DB.XYZ(*direction).Normalize()
        right = forward.CrossProduct(DB.XYZ(*vertical)).Normalize()
        up = right.CrossProduct(forward).Normalize()
        view.SetOrientation(DB.ViewOrientation3D(center-forward*100, up, forward))
        geo.tag(view, "presentation/" + name + "/" + run_id, run_id)
        views.append(view)
    return views


def export_views(doc, output, views):
    options = DB.ImageExportOptions()
    try:
        options.FilePath = os.path.join(output, "details")
        options.ExportRange = DB.ExportRange.SetOfViews
        options.SetViewsAndSheets(List[DB.ElementId]([v.Id for v in views]))
        options.ZoomType = DB.ZoomFitType.FitToPage
        options.PixelSize = 2000
        options.HLRandWFViewsFileType = DB.ImageFileType.PNG
        options.ShadowViewsFileType = DB.ImageFileType.PNG
        doc.ExportImage(options)
        return {"available": True, "paths": [os.path.join(output, f) for f in os.listdir(output) if f.lower().endswith(".png")]}
    finally:
        options.Dispose()


def build_detail(doc, item, run_id, result):
    def finish(element, plane, method):
        rgb = item.get("material_rgb", [180, 180, 180])
        name = "MCP visual " + "-".join(str(x) for x in rgb)
        found = [m for m in audit.collect(doc, DB.Material) if m.Name == name]
        material = found[0] if found else doc.GetElement(DB.Material.Create(doc, name))
        if not found:
            material.Color = DB.Color(*rgb)
        parameter = element.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
        audit.require(parameter is not None and not parameter.IsReadOnly, "Material parameter unavailable")
        parameter.Set(material.Id)
        if item.get("fine_only", False):
            visibility = DB.FamilyElementVisibility(DB.FamilyElementVisibilityType.Model)
            visibility.IsShownInFine = True
            visibility.IsShownInMedium = False
            visibility.IsShownInCoarse = False
            element.SetVisibility(visibility)
        geo.tag(element, item["logical_id"], run_id)
        if plane is not None:
            geo.tag(plane, item["logical_id"] + "/plane", run_id)
        doc.Regenerate()
        options = DB.Options()
        options.DetailLevel = DB.ViewDetailLevel.Fine
        options.IncludeNonVisibleObjects = True
        audit.require(audit.solid_count(element.get_Geometry(options)) == 1, "Detail must contain exactly one nonempty solid")
        measured = audit.box_data(element)
        validation = None
        if item.get("expected_bounds_mm"):
            validation = compare_bounds(measured, item["expected_bounds_mm"], result["tolerance_mm"])
            audit.require(validation["success"], "Detail bounds differ from specification")
        return {"element_id": element.Id.IntegerValue, "unique_id": element.UniqueId,
                "logical_id": item["logical_id"], "method": method, "bbox": measured,
                "bounds_validation": validation, "accuracy": item["accuracy"], "source": item["source"]}
    def native():
        element, plane = shapes.native_extrusion(doc, item["geometry"])
        return finish(element, plane, "native_extrusion")
    def freeform():
        solid = shapes.solid(item["geometry"])
        audit.require(solid is not None and solid.Volume > 0, "Empty or invalid constructed solid")
        audit.require(DB.SolidUtils.SplitVolumes(solid).Count == 1, "Disconnected solids must be separate details")
        return finish(DB.FreeFormElement.Create(doc, solid), None, "freeform")
    if item["geometry"]["kind"] == "extrusion":
        # A full transaction handles errors discovered only at commit. Failed native
        # attempts leave no sketch, material or geometry behind before the fallback.
        attempt = {"errors": [], "warnings": []}
        try:
            value = ops.transaction(doc, "Detail extrusion " + item["logical_id"], native, attempt)
            result["warnings"].extend(attempt["warnings"])
            return value
        except Exception as error:
            result["warnings"].append(item["logical_id"] + ": native extrusion rolled back; trying identical solid as FreeForm: " + unicode(error))
            result.setdefault("native_fallbacks", []).append({"logical_id": item["logical_id"], "error": unicode(error), "failures": attempt})
    return ops.transaction(doc, "Detail FreeForm " + item["logical_id"], freeform, result)


def replace_forms(doc, records):
    allowed = set(r["element_id"] for r in records)
    elements = []
    for record in records:
        element = doc.GetElement(DB.ElementId(record["element_id"]))
        audit.require(isinstance(element, DB.GenericForm) and element.UniqueId == record["unique_id"], "Replacement is not the planned form")
        elements.append(element)
        # Only sketch internals may disappear together with the requested form.
        # Dependent dimensions, connectors, user forms, etc. cause a rollback.
        for cls in (DB.Sketch, DB.SketchPlane, DB.CurveElement):
            for dependent in element.GetDependentElements(DB.ElementClassFilter(cls)):
                allowed.add(dependent.IntegerValue)
    deleted = set()
    for element in elements:
        if doc.GetElement(element.Id) is not None:
            deleted.update(x.IntegerValue for x in doc.Delete(element.Id))
    audit.require(deleted.issubset(allowed), "Replacement would delete protected dependent elements; use a clean base family")
    doc.Regenerate()
    return sorted(deleted)


def dispatch(uiapp, data, root):
    before = audit.preflight(uiapp)
    doc = audit.active_document(uiapp)
    owned = [g for g in geo.generated(doc) if g.get("created_by") == "FamilyMCP"]
    result = detail_plan(before, data.get("spec", {}), owned)
    result.update(dry_run=data.get("dry_run", True), preflight=before, saved=False, model_committed=False,
                  transaction_group_rolled_back=False, created_details=[])
    audit.require(type(result["dry_run"]) is bool, "dry_run must be boolean")
    # Source identity is checked in dry-run as well as immediately before mutation.
    if result["ready"]:
        for source in result["source_documents"]:
            try:
                audit.require(ops.hash_file(source["file"]) == source["sha256"], "Source drawing changed")
            except Exception as error:
                result["errors"].append(unicode(error))
                result["ready"] = False
    if result["dry_run"]:
        return result
    audit.require(data.get("context_token") == before["context_token"], "Stale detail context token")
    if not result["ready"]:
        result["success"] = False
        return result
    run_id = str(uuid.uuid4())
    output, backup = os.path.join(root, "tests", "output", run_id), os.path.join(root, "backup", run_id)
    os.makedirs(output)
    os.makedirs(backup)
    original_path = doc.PathName
    original_hash = ops.hash_file(original_path)
    result.update(run_id=run_id, backup_path=os.path.join(backup, "before_details.rfa"),
                  output_path=os.path.join(output, "Detailed_Family.rfa"))
    group = None
    try:
        result["stage"] = "backup"
        ops.save_as(doc, result["backup_path"])
        group = DB.TransactionGroup(doc, "MCP universal details")
        audit.require(group.Start() == DB.TransactionStatus.Started, "Could not start detail group")
        result["stage"] = "replacement"
        if result["replacement_elements"]:
            result["deleted_element_ids"] = ops.transaction(doc, "Replace selected generated forms",
                lambda: replace_forms(doc, result["replacement_elements"]), result)
        result["stage"] = "geometry"
        for item in result["details"]:
            result["current_detail"] = item["logical_id"]
            result["created_details"].append(build_detail(doc, item, run_id, result))
        result["stage"] = "validation"
        retained = copy.deepcopy(before)
        replaced = set(r["unique_id"] for r in result["replacement_elements"])
        retained["existing_geometry"]["elements"] = [e for e in retained["existing_geometry"]["elements"] if e["unique_id"] not in replaced]
        result["preservation"] = ops.preserved(doc, retained, set())
        audit.require(doc.FamilyManager.Parameters.Size == before["parameter_count"], "Detailing changed parameter count")
        after = audit.existing_geometry(doc)
        audit.require(after["element_count"] == before["existing_geometry"]["element_count"] - len(replaced) + len(result["details"]), "Unexpected form count")
        bounded = [e for e in after["elements"] if e.get("solids")]
        audit.require(bounded and all(e.get("bbox") for e in bounded), "Cannot measure assembly solids")
        audit.require(all(e.get("solids") is not None for e in after["elements"]), "Cannot inspect all assembly geometry")
        actual = {"min_mm": [min(e["bbox"]["min_mm"][i] for e in bounded) for i in range(3)],
                  "max_mm": [max(e["bbox"]["max_mm"][i] for e in bounded) for i in range(3)]}
        result["assembly_validation"] = compare_bounds(actual, result["expected_bounds_mm"], result["tolerance_mm"])
        audit.require(result["assembly_validation"]["success"], "Assembly bounds differ from specification")
        result["stage"] = "views"
        views = ops.transaction(doc, "Create drawing comparison views", lambda: create_views(doc, run_id, actual), result)
        result["view_ids"] = [v.Id.IntegerValue for v in views]
        audit.require(group.Assimilate() == DB.TransactionStatus.Committed, "Detail group did not commit")
        group.Dispose()
        group = None
        result["model_committed"] = True
        result["stage"] = "save"
        ops.save_as(doc, result["output_path"])
        result["saved"] = True
        try:
            uiapp.ActiveUIDocument.ActiveView = views[0]
            for ui_view in uiapp.ActiveUIDocument.GetOpenUIViews():
                if ui_view.ViewId == views[0].Id:
                    ui_view.ZoomToFit()
            result["preview"] = export_views(doc, output, views)
        except Exception as error:
            result["preview"] = {"available": False, "error": unicode(error)}
            result["warnings"].append("Preview export failed; visual comparison remains required")
        result["original_file_unchanged"] = ops.hash_file(original_path) == original_hash
        audit.require(result["original_file_unchanged"], "Original RFA changed on disk")
        result.update(success=True, stage="complete")
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
    before = audit.inspect(uiapp)
    doc, view = audit.active_document(uiapp), uiapp.ActiveUIDocument.ActiveView
    tagged = [g for g in geo.generated(doc) if g.get("created_by") == "FamilyMCP"
              and g["element_id"] == view.Id.IntegerValue and g["logical_id"].startswith("presentation/")]
    audit.require(isinstance(view, DB.View3D) and tagged, "Activate a generated MCP detail comparison view")
    result = {"success": True, "ready": True, "dry_run": data.get("dry_run", True), "context_token": before["context_token"],
              "errors": [], "warnings": [], "saved": False, "model_committed": False, "transaction_group_rolled_back": False}
    audit.require(type(result["dry_run"]) is bool, "dry_run must be boolean")
    if result["dry_run"]:
        return result
    audit.require(data.get("context_token") == before["context_token"], "Stale presentation context")
    run_id = str(uuid.uuid4())
    backup, output = os.path.join(root, "backup", run_id), os.path.join(root, "tests", "output", run_id)
    os.makedirs(backup)
    os.makedirs(output)
    result.update(backup_path=os.path.join(backup, "before_presentation.rfa"), output_path=os.path.join(output, "Detailed_Family.rfa"))
    original_path, original_hash = doc.PathName, ops.hash_file(doc.PathName)
    group = None
    try:
        ops.save_as(doc, result["backup_path"])
        group = DB.TransactionGroup(doc, "MCP detail presentation")
        audit.require(group.Start() == DB.TransactionStatus.Started, "Could not start presentation group")
        def style():
            view.Scale = 20
            settings = DB.OverrideGraphicSettings()
            settings.SetProjectionLineWeight(1)
            settings.SetProjectionLineColor(DB.Color(65, 65, 65))
            view.SetCategoryOverrides(doc.OwnerFamily.FamilyCategory.Id, settings)
        ops.transaction(doc, "Detail preview line weights", style, result)
        result["validation"] = ops.preserved(doc, before, set())
        audit.require(group.Assimilate() == DB.TransactionStatus.Committed, "Presentation group did not commit")
        group.Dispose()
        group = None
        result["model_committed"] = True
        ops.save_as(doc, result["output_path"])
        result["saved"] = True
        try:
            result["preview"] = export_views(doc, output, [view])
        except Exception as error:
            result["warnings"].append("Preview export failed: " + unicode(error))
        result["original_file_unchanged"] = ops.hash_file(original_path) == original_hash
        audit.require(result["original_file_unchanged"], "Original RFA changed")
    except Exception as error:
        result.update(success=False, error=unicode(error), traceback=traceback.format_exc())
        result["errors"].append(unicode(error))
    finally:
        if group is not None:
            if group.GetStatus() == DB.TransactionStatus.Started:
                result["transaction_group_rolled_back"] = group.RollBack() == DB.TransactionStatus.RolledBack
            group.Dispose()
        result["active_rfa_path_after"] = doc.PathName
    return result
