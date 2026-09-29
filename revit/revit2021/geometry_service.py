# -*- coding: utf-8 -*-
"""Parametric box for the active-family integration test. Revit API 2021 only."""
from System import Guid, String
import math
from pyrevit import DB
from revit.revit2021.active_family import require, xyz, solid_count
from revit.revit2021.unit_converter import UnitConverter as Units

SCHEMA_GUID = Guid("526d3528-d228-44cd-88cf-0c9a99146f25")


def schema(create=False):
    existing = DB.ExtensibleStorage.Schema.Lookup(SCHEMA_GUID)
    if existing is not None or not create:
        return existing
    builder = DB.ExtensibleStorage.SchemaBuilder(SCHEMA_GUID)
    builder.SetSchemaName("FamilyMCPGeometryV1")
    for name in ("created_by", "logical_id", "spec_version", "run_id"):
        builder.AddSimpleField(name, String)
    return builder.Finish()


def tag(element, logical_id, run_id):
    definition = schema(True)
    entity = DB.ExtensibleStorage.Entity(definition)
    for key, value in {"created_by": "FamilyMCP", "logical_id": logical_id,
                       "spec_version": "1", "run_id": run_id}.items():
        entity.Set[String](definition.GetField(key), value)
    element.SetEntity(entity)


def generated(doc):
    definition = schema()
    if definition is None:
        return []
    result = []
    for element in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        entity = element.GetEntity(definition)
        if entity.IsValid():
            result.append({"unique_id": element.UniqueId, "element_id": element.Id.IntegerValue,
                           "logical_id": entity.Get[String](definition.GetField("logical_id")),
                           "created_by": entity.Get[String](definition.GetField("created_by"))})
    return result


def _references(planes):
    refs = DB.ReferenceArray()
    for plane in planes:
        refs.Append(plane.GetReference())
    return refs


def _side_faces(extrusion):
    options = DB.Options()
    options.ComputeReferences = True
    faces = {}
    for solid in extrusion.get_Geometry(options):
        if not isinstance(solid, DB.Solid) or solid.Volume <= 0:
            continue
        for face in solid.Faces:
            if not isinstance(face, DB.PlanarFace):
                continue
            normal = face.FaceNormal
            for name, direction in (("left", DB.XYZ(-1, 0, 0)), ("right", DB.XYZ.BasisX),
                                    ("front", DB.XYZ(0, -1, 0)), ("back", DB.XYZ.BasisY)):
                if normal.DotProduct(direction) > 0.999999:
                    require(name not in faces, "Ambiguous box face: " + name)
                    require(face.Reference is not None, "Face reference is missing")
                    faces[name] = face.Reference
    require(len(faces) == 4, "Could not identify four box side faces by normal")
    return faces


def create_parametric_box(doc, coordinate, parameter_map, dimensions_mm, logical_id, run_id):
    require(not any(e["logical_id"] == logical_id for e in generated(doc)), "Generated logical_id already exists")
    origin = [Units.mm_to_internal(x) for x in coordinate["origin_mm"]]
    cx, cy, z = origin
    w, d, h = [Units.mm_to_internal(x) for x in dimensions_mm]
    view = doc.GetElement(DB.ElementId(coordinate["plan_view_id"]))
    auxiliary = []
    planes = {}
    for name, x in (("left", cx - w / 2), ("center_x", cx), ("right", cx + w / 2)):
        plane = doc.FamilyCreate.NewReferencePlane(DB.XYZ(x, cy - d, z), DB.XYZ(x, cy + d, z), DB.XYZ.BasisZ, view)
        plane.Name = "FamilyMCP_" + logical_id + "_" + name
        plane.Pinned = name == "center_x"
        planes[name] = plane
        auxiliary.append(plane)
    for name, y in (("front", cy - d / 2), ("center_y", cy), ("back", cy + d / 2)):
        plane = doc.FamilyCreate.NewReferencePlane(DB.XYZ(cx - w, y, z), DB.XYZ(cx + w, y, z), DB.XYZ.BasisZ, view)
        plane.Name = "FamilyMCP_" + logical_id + "_" + name
        plane.Pinned = name == "center_y"
        planes[name] = plane
        auxiliary.append(plane)
    # Revit must materialize datum geometry before its references can be used
    # by a dimension in the same transaction.
    doc.Regenerate()
    margin = Units.mm_to_internal(500)
    for axis, names, parameter in ((0, ("left", "center_x", "right"), parameter_map["width"]),
                                   (1, ("front", "center_y", "back"), parameter_map["depth"])):
        if axis == 0:
            line = DB.Line.CreateBound(DB.XYZ(cx - w, cy - d - margin, z), DB.XYZ(cx + w, cy - d - margin, z))
            equal_line = DB.Line.CreateBound(DB.XYZ(cx - w, cy - d - 2 * margin, z), DB.XYZ(cx + w, cy - d - 2 * margin, z))
        else:
            line = DB.Line.CreateBound(DB.XYZ(cx - w - margin, cy - d, z), DB.XYZ(cx - w - margin, cy + d, z))
            equal_line = DB.Line.CreateBound(DB.XYZ(cx - w - 2 * margin, cy - d, z), DB.XYZ(cx - w - 2 * margin, cy + d, z))
        for name in names:
            normal = planes[name].GetPlane().Normal
            require(abs(normal.DotProduct(line.Direction)) > 0.999999,
                    "Dimension is not perpendicular to reference plane: " + name + " normal=" + str(xyz(normal)))
        try:
            dimension = doc.FamilyCreate.NewLinearDimension(view, line, _references([planes[names[0]], planes[names[2]]]))
        except Exception as error:
            raise RuntimeError("Box dimension axis=" + str(axis) + " view=" + str(view.ViewType) +
                               " direction=" + str(xyz(view.ViewDirection)) + ": " + unicode(error))
        dimension.FamilyLabel = parameter
        equal = doc.FamilyCreate.NewLinearDimension(view, equal_line, _references([planes[n] for n in names]))
        equal.AreSegmentsEqual = True
        auxiliary.extend([dimension, equal])
    sketch_plane = DB.SketchPlane.Create(doc, DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ(cx, cy, z)))
    auxiliary.append(sketch_plane)
    corners = [DB.XYZ(cx - w / 2, cy - d / 2, z), DB.XYZ(cx + w / 2, cy - d / 2, z),
               DB.XYZ(cx + w / 2, cy + d / 2, z), DB.XYZ(cx - w / 2, cy + d / 2, z)]
    profile = DB.CurveArray()
    for i in range(4):
        profile.Append(DB.Line.CreateBound(corners[i], corners[(i + 1) % 4]))
    profiles = DB.CurveArrArray()
    profiles.Append(profile)
    extrusion = doc.FamilyCreate.NewExtrusion(True, profiles, sketch_plane, h)
    doc.FamilyManager.AssociateElementParameterToFamilyParameter(
        extrusion.get_Parameter(DB.BuiltInParameter.EXTRUSION_END_PARAM), parameter_map["height"])
    doc.Regenerate()
    for name, reference in _side_faces(extrusion).items():
        auxiliary.append(doc.FamilyCreate.NewAlignment(view, planes[name].GetReference(), reference))
    tag(extrusion, logical_id, run_id)
    for index, element in enumerate(auxiliary):
        tag(element, logical_id + "/support/" + str(index), run_id)
    return extrusion, auxiliary


def measure_box(extrusion, coordinate, requested_mm, cylinder=False):
    points, volume, solids = [], 0.0, 0
    cylinder_faces = []
    for item in extrusion.get_Geometry(DB.Options()):
        if isinstance(item, DB.Solid) and item.Volume > 1e-12:
            solids += 1
            volume += item.Volume
            for face in item.Faces:
                if cylinder:
                    require(isinstance(face, (DB.CylindricalFace, DB.PlanarFace)), "Unexpected cylinder surface")
                    if isinstance(face, DB.CylindricalFace):
                        radii = [float(Units.internal_to_mm(face.get_Radius(i).GetLength())) for i in (0, 1)]
                        require(max(abs(r - requested_mm[1] / 2) for r in radii) <= 0.001,
                                "Analytic cylinder radius mismatch")
                        require(abs(face.Axis.Normalize().X) > 0.999999, "Cylinder axis mismatch")
                        cylinder_faces.append({"radii_mm": radii, "axis": xyz(face.Axis)})
                for vertex in face.Triangulate().Vertices:
                    points.append(xyz(vertex, True))
    require(bool(points) and solids > 0, "Generated box has no solid geometry")
    minimum = [min(p[i] for p in points) for i in range(3)]
    maximum = [max(p[i] for p in points) for i in range(3)]
    sizes = [maximum[i] - minimum[i] for i in range(3)]
    expected_min = [coordinate["origin_mm"][0] - requested_mm[0] / 2,
                    coordinate["origin_mm"][1] - requested_mm[1] / 2, coordinate["origin_mm"][2]]
    if cylinder:
        # Revit's analytic element box captures exact cylinder extrema; tessellation
        # alone can undershoot the radius depending on display accuracy.
        box = extrusion.get_BoundingBox(None)
        minimum, maximum = xyz(box.Min, True), xyz(box.Max, True)
        sizes = [maximum[i] - minimum[i] for i in range(3)]
        expected_min[2] -= requested_mm[2] / 2
    delta = max([abs(sizes[i] - requested_mm[i]) for i in range(3)] +
                [abs(minimum[i] - expected_min[i]) for i in range(3)])
    expected_volume = 1.0
    for value in requested_mm:
        expected_volume *= Units.mm_to_internal(value)
    if cylinder:
        expected_volume *= math.pi / 4
        require(solids == 1 and bool(cylinder_faces), "Expected one analytic cylindrical solid")
    require(delta <= 0.001, "Box dimensions or origin failed flex validation: delta_mm=" + str(delta))
    # RevitAPI.xml Solid.Volume documents an approximation for curved faces.
    # Bounds and analytic radii above remain subject to the strict 0.001 mm check.
    volume_tolerance = 1e-4 if cylinder else 1e-6
    require(abs(volume - expected_volume) <= max(1e-9, expected_volume * volume_tolerance),
            "Invalid solid volume: element_id={0}, cylinder={1}, actual={2}, expected={3}, relative_error={4}".format(
                extrusion.Id.IntegerValue, cylinder, volume, expected_volume, abs(volume / expected_volume - 1)))
    return {"requested_mm": requested_mm, "bbox_min_mm": minimum, "bbox_max_mm": maximum,
            "bbox_size_mm": sizes, "max_delta_mm": delta, "solids": solids,
            "volume_internal": volume, "expected_volume_internal": expected_volume,
            "volume_relative_error": abs(volume / expected_volume - 1),
            "volume_relative_tolerance": volume_tolerance,
            "analytic_cylinder_faces": cylinder_faces, "success": True}


def body_coordinate(coordinate, body):
    result = dict(coordinate)
    result["origin_mm"] = [a + b for a, b in zip(coordinate["origin_mm"], body.get("offset_mm", [0, 0, 0]))]
    return result


def create_cylinder(doc, coordinate, parameters, dimensions_mm, logical_id, run_id):
    require(not any(e["logical_id"] == logical_id for e in generated(doc)), "Generated logical_id already exists")
    center = DB.XYZ(*[Units.mm_to_internal(v) for v in coordinate["origin_mm"]])
    length, diameter, unused = [Units.mm_to_internal(v) for v in dimensions_mm]
    plane = DB.SketchPlane.Create(doc, DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisX, center))
    circle = DB.Arc.Create(center, diameter / 2, 0, 2 * math.pi, DB.XYZ.BasisY, DB.XYZ.BasisZ)
    profile, profiles = DB.CurveArray(), DB.CurveArrArray()
    profile.Append(circle)
    profiles.Append(profile)
    extrusion = doc.FamilyCreate.NewExtrusion(True, profiles, plane, length / 2)
    for builtin, key in ((DB.BuiltInParameter.EXTRUSION_START_PARAM, "start"), (DB.BuiltInParameter.EXTRUSION_END_PARAM, "end")):
        doc.FamilyManager.AssociateElementParameterToFamilyParameter(extrusion.get_Parameter(builtin), parameters[key])
    doc.Regenerate()
    views = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewSection)
             if not v.IsTemplate and abs(v.ViewDirection.X) > 0.999999]
    require(bool(views), "Cylinder radius needs an existing elevation looking along family X")
    arc = next(curve for loop in extrusion.Sketch.Profile for curve in loop if isinstance(curve, DB.Arc))
    reference = arc.Reference
    if reference is None:
        for element_id in extrusion.Sketch.GetDependentElements(DB.ElementClassFilter(DB.CurveElement)):
            element = doc.GetElement(element_id)
            if isinstance(element.GeometryCurve, DB.Arc) and element.GeometryCurve.Reference is not None:
                reference = element.GeometryCurve.Reference
                break
    require(reference is not None, "Cylinder sketch circle has no dimension reference")
    dimension = doc.FamilyCreate.NewRadialDimension(views[0], reference,
        center + DB.XYZ(0, diameter, diameter))
    dimension.FamilyLabel = parameters["radius"]
    doc.Regenerate()
    tag(extrusion, logical_id, run_id)
    auxiliary = [plane, dimension]
    for index, element in enumerate(auxiliary):
        tag(element, logical_id + "/support/" + str(index), run_id)
    return extrusion, auxiliary
