# -*- coding: utf-8 -*-
import json
import os
import sys
root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if root not in sys.path:
    sys.path.insert(0, root)
from pyrevit import HOST_APP

# Refresh only this project's handlers in the current engine. Reuse the running
# Routes listener; do not reload pyRevit or restart other installed extensions.
if str(HOST_APP.version) == "2021":
    if HOST_APP.uiapp.ActiveUIDocument and HOST_APP.uiapp.ActiveUIDocument.Document.IsModifiable:
        raise RuntimeError("Finish the current model transaction before refreshing MCP")
    for module_name in list(sys.modules):
        if module_name == "core" or module_name.startswith("core.") or module_name.startswith("revit.revit2021"):
            del sys.modules[module_name]
    startup = os.path.join(root, "extensions", "MCPHealth.extension", "startup.py")
    namespace = {"__file__": startup, "__name__": "family_mcp_refresh"}
    execfile(startup, namespace)

from revit.revit2021.health_service import revit_health_check

print(json.dumps(revit_health_check(HOST_APP.uiapp), ensure_ascii=True, indent=2))
