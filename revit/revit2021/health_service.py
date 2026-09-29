# -*- coding: utf-8 -*-
"""Read-only diagnostics, executed by pyRevit Routes ExternalEvent."""
import platform
import sys

import clr
from System import DateTime
from System.Diagnostics import Process
from pyrevit import DB, UI, routes, versionmgr
from pyrevit.coreutils import envvars


def registered_routes():
    registry = envvars.get_pyrevit_env_var(envvars.ROUTES_ROUTES) or {}
    result = []
    for api_name, route_map in registry.items():
        for route in route_map:
            result.append({"api": api_name, "method": route.method,
                           "path": "/" + api_name + route.pattern})
    return sorted(result, key=lambda item: (item["path"], item["method"]))


def revit_health_check(uiapp):
    """uiapp argument forces execution on Revit's API thread, even without a doc."""
    app = uiapp.Application
    db_assembly = clr.GetClrType(DB.Document).Assembly
    ui_assembly = clr.GetClrType(UI.UIApplication).Assembly
    db_version = str(db_assembly.GetName().Version)
    ui_version = str(ui_assembly.GetName().Version)
    pyrevit_version = versionmgr.get_pyrevit_version().get_formatted()
    uidoc = uiapp.ActiveUIDocument
    doc = uidoc.Document if uidoc else None
    server = routes.get_active_server()
    errors = []
    if str(app.VersionNumber) != "2021":
        errors.append("Expected Revit 2021")
    if db_version != "21.0.0.0" or ui_version != "21.0.0.0":
        errors.append("Expected RevitAPI and RevitAPIUI assembly version 21.0.0.0")
    if not pyrevit_version.startswith("5.1."):
        errors.append("Expected pyRevit 5.1.x")
    if not server:
        errors.append("pyRevit Routes server is not active")
    return {
        "success": not errors,
        "errors": errors,
        "revit_version": str(app.VersionNumber),
        "revit_build": str(app.VersionBuild),
        "revit_product": str(app.VersionName),
        "revit_api_version": db_version,
        "revit_apiui_version": ui_version,
        "revit_api_path": db_assembly.Location,
        "revit_apiui_path": ui_assembly.Location,
        "pyrevit_version": pyrevit_version,
        "python_engine": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_runtime": sys.version,
        "document_title": doc.Title if doc else None,
        "is_family_document": bool(doc.IsFamilyDocument) if doc else False,
        "has_active_document": doc is not None,
        "routes_available": bool(server),
        "registered_routes": registered_routes(),
        "process_id": Process.GetCurrentProcess().Id,
        "checked_at_utc": DateTime.UtcNow.ToString("o"),
        "api_context": "pyRevit Routes ExternalEvent / UIApplication",
        "bridge_version": "0.1.0",
    }
