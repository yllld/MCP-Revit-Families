# -*- coding: utf-8 -*-
"""Register health diagnostics and the isolated family test in Revit 2021."""
import io
import json
import os
import sys

from pyrevit import HOST_APP


def start():
    if str(HOST_APP.version) != "2021":
        return
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from pyrevit import routes
    from pyrevit.userconfig import user_config
    from revit.revit2021.health_service import revit_health_check
    from revit.revit2021 import active_family
    from core.json_transport import ascii_envelope

    api = routes.API("mcp-health")

    @api.route("/health", methods=["GET"])
    def health(uiapp):
        # Catch here: this pyRevit version's generic exception wrapper assumes
        # every Python exception has clsException, which is not always true.
        try:
            return revit_health_check(uiapp)
        except Exception as error:
            return routes.make_response(
                {"success": False, "error": "REVIT_HEALTH_FAILED",
                 "message": str(error)}, status=500)

    family_api = routes.API("family-mcp")

    @family_api.route("/active/<operation>", methods=["POST"])
    def active_family_operation(uiapp, request, operation):
        import traceback
        from System.Diagnostics import Process
        try:
            payload = request.data or {}
            if payload.get("expected_process_id") != Process.GetCurrentProcess().Id:
                raise RuntimeError("Revit process identity mismatch")
            if operation == "context":
                result = active_family.context(uiapp)
            elif operation == "inspect":
                result = active_family.inspect(uiapp)
            elif operation == "preflight":
                result = active_family.preflight(uiapp)
            elif operation == "detail":
                from revit.revit2021 import detail_operations
                result = detail_operations.dispatch(uiapp, payload.get("data", {}), root)
            elif operation == "presentation":
                from revit.revit2021 import detail_operations
                result = detail_operations.presentation(uiapp, payload.get("data", {}), root)
            else:
                from revit.revit2021 import active_operations
                result = active_operations.dispatch(uiapp, operation, payload.get("data", {}), root)
        except Exception as error:
            result = {"success": False, "stage": operation, "error_type": type(error).__name__,
                      "error": unicode(error), "traceback": traceback.format_exc(),
                      "errors": [unicode(error)], "warnings": []}
        result["process_id"] = Process.GetCurrentProcess().Id
        result["request_id"] = (request.data or {}).get("request_id")
        # Its bundled JSON encoder mishandles Cyrillic mixed with symbols such
        # as superscript 3. Transfer only ASCII to the Routes response encoder.
        return ascii_envelope(result)

    active = routes.get_active_server()
    if active and active.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Existing Routes server is not loopback-only")
    if not active:
        # In-memory configuration only. Do not enable Routes globally for 2025.
        original_host = user_config.routes_host
        original_port = user_config.routes_port
        try:
            # pyRevit's allocator checks its pickle registry, not live sockets.
            # On Windows a previous Reload can leave a listener on that port;
            # SO_REUSEADDR then binds a second server whose requests never arrive.
            from System.Net.NetworkInformation import IPGlobalProperties
            from pyrevit.routes.server import serverinfo
            occupied = set(endpoint.Port for endpoint in IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners())
            port = original_port
            while port in occupied:
                port += 1
            if port > 65535:
                raise RuntimeError("No available Routes port")
            serverinfo.unregister()  # Only this Revit process's stale Routes registration.
            user_config.routes_host = "127.0.0.1"
            user_config.routes_port = port
            routes.active_routes_api()
            active = routes.activate_server()
        finally:
            user_config.routes_host = original_host
            user_config.routes_port = original_port
    if not active:
        raise RuntimeError("Could not start pyRevit Routes")
    runtime_dir = os.path.join(root, ".runtime")
    if not os.path.isdir(runtime_dir):
        os.makedirs(runtime_dir)
    endpoint = {
        "revit_version": "2021",
        "process_id": HOST_APP.proc_id,
        "url": "http://127.0.0.1:{0}/mcp-health/health".format(active.port),
    }
    path = os.path.join(runtime_dir, "revit2021-{0}.json".format(HOST_APP.proc_id))
    with io.open(path, "w", encoding="utf-8") as stream:
        stream.write(unicode(json.dumps(endpoint, ensure_ascii=True, indent=2)))


start()
