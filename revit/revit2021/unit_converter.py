# -*- coding: utf-8 -*-
"""Revit 2021 units. This module runs in IronPython 2.7 only."""
from System import Double
from pyrevit import DB


class UnitConverter(object):
    @staticmethod
    def mm_to_internal(value):
        return DB.UnitUtils.ConvertToInternalUnits(
            Double(value), DB.DisplayUnitType.DUT_MILLIMETERS)

    @staticmethod
    def internal_to_mm(value):
        return DB.UnitUtils.ConvertFromInternalUnits(
            Double(value), DB.DisplayUnitType.DUT_MILLIMETERS)
