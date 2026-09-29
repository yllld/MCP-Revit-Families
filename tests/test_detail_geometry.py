"""Numeric coordinate tests with a small API double; no claim of Revit kernel execution."""
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest


class XYZ:
    def __init__(self, x, y, z):
        self.values = (x, y, z)

    def __add__(self, other):
        return XYZ(*[a+b for a, b in zip(self.values, other.values)])

    def __sub__(self, other):
        return XYZ(*[a-b for a, b in zip(self.values, other.values)])

    def __mul__(self, scale):
        return XYZ(*[a*scale for a in self.values])

    def CrossProduct(self, other):
        x, y, z = self.values
        a, b, c = other.values
        return XYZ(y*c-z*b, z*a-x*c, x*b-y*a)


class Array(list):
    Append = list.append


class Generic:
    def __getitem__(self, cls):
        return list


def load_module(path, db, extra=None):
    namespace = {"__name__": "offline_detail_module", "unicode": str}
    modules = {"pyrevit": SimpleNamespace(DB=db), "System.Collections.Generic": SimpleNamespace(List=Generic())}
    modules.update(extra or {})
    with patch.dict("sys.modules", modules):
        exec(compile(Path(path).read_text(encoding="utf-8"), path, "exec"), namespace)
    return SimpleNamespace(**namespace)


class ProfileGeometryTests(unittest.TestCase):
    def setUp(self):
        def line(a, b):
            return SimpleNamespace(GetEndPoint=lambda i: (a, b)[i])
        def arc(c, radius, a, b, u, v):
            return line(c+u*(radius*math.cos(a))+v*(radius*math.sin(a)),
                        c+u*(radius*math.cos(b))+v*(radius*math.sin(b)))
        db = SimpleNamespace(XYZ=XYZ, CurveLoop=Array, CurveArray=Array, CurveArrArray=Array,
                             Line=SimpleNamespace(CreateBound=line), Arc=SimpleNamespace(Create=arc))
        units = SimpleNamespace(UnitConverter=SimpleNamespace(mm_to_internal=lambda x: x/304.8))
        self.api = load_module("revit/revit2021/detail_geometry.py", db, {"revit.revit2021.unit_converter": units})
        self.db = db

    def assertPoint(self, point, mm):
        for a, b in zip(point.values, mm):
            self.assertAlmostEqual(a*304.8, b, places=6)

    def test_offset_rectangle_on_rotated_plane(self):
        loop = self.api.profile({"kind": "rectangle", "width_mm": 20, "height_mm": 10, "offset_mm": [3, 4]},
                                self.api.point([100, 200, 300]), XYZ(0, 1, 0), XYZ(0, 0, 1))
        self.assertPoint(loop[0].GetEndPoint(0), [100, 193, 299])
        self.assertPoint(loop[2].GetEndPoint(0), [100, 213, 309])

    def test_rounded_rectangle_has_four_true_arcs_and_closed_links(self):
        loop = self.api.profile({"kind": "rounded_rectangle", "width_mm": 40, "height_mm": 20, "radius_mm": 3},
                                self.api.point([0, 0, 0]), XYZ(1, 0, 0), XYZ(0, 1, 0))
        self.assertEqual(len(loop), 8)
        for a, b in zip(loop, loop[1:]+loop[:1]):
            for x, y in zip(a.GetEndPoint(1).values, b.GetEndPoint(0).values):
                self.assertAlmostEqual(x, y)

    def test_extrusion_direction_depth_and_holes(self):
        self.db.GeometryCreationUtilities = SimpleNamespace(CreateExtrusionGeometry=Mock(return_value="solid"))
        shape = {"kind": "extrusion", "frame": {"origin_mm": [10, 20, 30], "u": [1, 0, 0], "v": [0, 0, 1]},
                 "loops": [{"kind": "circle", "radius_mm": 20}, {"kind": "circle", "radius_mm": 10}], "depth_mm": 100}
        self.assertEqual(self.api.solid(shape), "solid")
        loops, normal, depth = self.db.GeometryCreationUtilities.CreateExtrusionGeometry.call_args.args
        self.assertEqual(len(loops), 2)
        self.assertEqual(normal.values, (0, -1, 0))
        self.assertAlmostEqual(depth*304.8, 100)

    def test_revolve_profile_uses_axial_frame_not_extrusion_normal(self):
        self.db.Frame = Mock(return_value="frame")
        self.db.GeometryCreationUtilities = SimpleNamespace(CreateRevolvedGeometry=Mock(return_value="solid"))
        shape = {"kind": "revolve", "frame": {"origin_mm": [0, 0, 50], "u": [1, 0, 0], "v": [0, 0, 1]},
                 "angle_deg": 180, "profile": {"kind": "polygon", "points_mm": [[0, 0], [20, 0], [20, 10], [0, 10]]}}
        self.api.solid(shape)
        self.assertEqual(self.db.Frame.call_args.args[3].values, (0, 0, 1))
        self.assertAlmostEqual(self.db.GeometryCreationUtilities.CreateRevolvedGeometry.call_args.args[3], math.pi)


class NativeFallbackTests(unittest.TestCase):
    def setUp(self):
        import revit.revit2021 as package
        self.audit = Mock()
        self.ops, self.shapes, self.geo, self.db = Mock(), Mock(), Mock(), Mock()
        extra = {"revit.revit2021." + key: value for key, value in {
            "active_family": self.audit, "active_operations": self.ops,
            "geometry_service": self.geo, "detail_geometry": self.shapes}.items()}
        with patch.multiple(package, active_family=self.audit, active_operations=self.ops,
                            geometry_service=self.geo, detail_geometry=self.shapes, create=True):
            self.api = load_module("revit/revit2021/detail_operations.py", self.db, extra)
        self.item = {"logical_id": "machine/body", "geometry": {"kind": "extrusion"}}
        self.result = {"errors": [], "warnings": [], "tolerance_mm": .1}

    def test_success_does_not_try_freeform(self):
        self.ops.transaction.return_value = {"method": "native_extrusion"}
        result = self.api.build_detail(Mock(), self.item, "run", self.result)
        self.assertEqual(result["method"], "native_extrusion")
        self.assertEqual(self.ops.transaction.call_count, 1)
        self.shapes.solid.assert_not_called()

    def test_commit_failure_uses_separate_fallback_transaction_and_reports_reason(self):
        self.ops.transaction.side_effect = [RuntimeError("native commit rolled back"), {"method": "freeform"}]
        result = self.api.build_detail(Mock(), self.item, "run", self.result)
        self.assertEqual(result["method"], "freeform")
        self.assertEqual(self.ops.transaction.call_count, 2)
        self.assertIn("native commit", self.result["native_fallbacks"][0]["error"])
        self.assertEqual(self.result["errors"], [])

    def test_both_failures_propagate_for_outer_group_rollback(self):
        self.ops.transaction.side_effect = [RuntimeError("native failure"), RuntimeError("solid failure")]
        with self.assertRaisesRegex(RuntimeError, "solid failure"):
            self.api.build_detail(Mock(), self.item, "run", self.result)

    def test_freeform_shapes_never_try_native_extrusion(self):
        self.item["geometry"]["kind"] = "boolean"
        self.ops.transaction.return_value = {"method": "freeform"}
        self.api.build_detail(Mock(), self.item, "run", self.result)
        self.assertEqual(self.ops.transaction.call_count, 1)
        self.assertIn("FreeForm", self.ops.transaction.call_args.args[1])
