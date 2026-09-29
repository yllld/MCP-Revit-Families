# -*- coding: utf-8 -*-
"""Analytic profiles and solids described by core.detail_plan, Revit 2021."""
from __future__ import division
import math
from System.Collections.Generic import List
from pyrevit import DB
from revit.revit2021.unit_converter import UnitConverter as Units


def point(values):
    return DB.XYZ(*[Units.mm_to_internal(v) for v in values])


def axes(frame):
    c, u, v = point(frame["origin_mm"]), DB.XYZ(*frame["u"]), DB.XYZ(*frame["v"])
    return c, u, v, u.CrossProduct(v)


def profile(spec, c, u, v):
    def p(values):
        return c + u * Units.mm_to_internal(values[0]) + v * Units.mm_to_internal(values[1])
    loop = DB.CurveLoop()
    kind = spec["kind"]
    if kind == "curves":
        for segment in spec["segments"]:
            a, b = p(segment["start_mm"]), p(segment["end_mm"])
            loop.Append(DB.Line.CreateBound(a, b) if segment["kind"] == "line" else DB.Arc.Create(a, b, p(segment["mid_mm"])))
    elif kind == "polygon":
        pts = [p(x) for x in spec["points_mm"]]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            loop.Append(DB.Line.CreateBound(a, b))
    else:
        c = p(spec.get("offset_mm", [0, 0]))
        if kind in ("circle", "ellipse"):
            for a, b in ((0, math.pi), (math.pi, 2*math.pi)):
                if kind == "circle":
                    curve = DB.Arc.Create(c, Units.mm_to_internal(spec["radius_mm"]), a, b, u, v)
                else:
                    curve = DB.Ellipse.CreateCurve(c, Units.mm_to_internal(spec["rx_mm"]), Units.mm_to_internal(spec["ry_mm"]), u, v, a, b)
                loop.Append(curve)
        else:
            w, h = spec["width_mm"] / 2, spec["height_mm"] / 2
            if kind == "rectangle":
                pts = [p(x) for x in ((-w, -h), (w, -h), (w, h), (-w, h))]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    loop.Append(DB.Line.CreateBound(a, b))
            else:
                r = spec["radius_mm"]
                arcs = [DB.Arc.Create(p([x, y]), Units.mm_to_internal(r), a, a+math.pi/2, u, v)
                        for x, y, a in ((w-r, h-r, 0), (-w+r, h-r, math.pi/2),
                                        (-w+r, -h+r, math.pi), (w-r, -h+r, 3*math.pi/2))]
                for index, arc in enumerate(arcs):
                    loop.Append(arc)
                    loop.Append(DB.Line.CreateBound(arc.GetEndPoint(1), arcs[(index+1) % 4].GetEndPoint(0)))
    return loop


def extrusion_profiles(spec):
    c, u, v, normal = axes(spec["frame"])
    return [profile(s, c, u, v) for s in spec["loops"]]


def native_extrusion(doc, spec):
    c, u, v, normal = axes(spec["frame"])
    profiles = DB.CurveArrArray()
    for loop in extrusion_profiles(spec):
        curves = DB.CurveArray()
        for curve in loop:
            curves.Append(curve)
        profiles.Append(curves)
    plane = DB.SketchPlane.Create(doc, DB.Plane.CreateByNormalAndOrigin(normal, c))
    element = doc.FamilyCreate.NewExtrusion(True, profiles, plane, Units.mm_to_internal(spec["depth_mm"]))
    return element, plane


def solid(spec):
    kind = spec["kind"]
    if kind == "boolean":
        operation = {"union": DB.BooleanOperationsType.Union, "difference": DB.BooleanOperationsType.Difference,
                     "intersection": DB.BooleanOperationsType.Intersect}[spec["operation"]]
        return DB.BooleanOperationsUtils.ExecuteBooleanOperation(solid(spec["left"]), solid(spec["right"]), operation)
    c, u, v, normal = axes(spec["frame"])
    if kind == "extrusion":
        return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop](extrusion_profiles(spec)), normal,
                                                                    Units.mm_to_internal(spec["depth_mm"]))
    if kind == "revolve":
        # Profile coordinates are [radius, axial height]; v is the axis of rotation.
        frame = DB.Frame(c, u, v.CrossProduct(u), v)
        return DB.GeometryCreationUtilities.CreateRevolvedGeometry(frame, List[DB.CurveLoop]([profile(spec["profile"], c, u, v)]),
                                                                   0, math.radians(spec["angle_deg"]))
    loops = [profile(section["profile"], c + normal*Units.mm_to_internal(section["offset_mm"]), u, v)
             for section in spec["sections"]]
    options = DB.SolidOptions(DB.ElementId.InvalidElementId, DB.ElementId.InvalidElementId)
    return DB.GeometryCreationUtilities.CreateLoftGeometry(List[DB.CurveLoop](loops), options)
