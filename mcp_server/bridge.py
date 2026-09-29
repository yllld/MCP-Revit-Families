"""Loopback-only HTTP client for the Revit 2021 pyRevit route."""
import json
import math
import os
from pathlib import Path
import socket
import threading
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
_lock = threading.Lock()  # pyRevit 5.1 has one shared ExternalEvent handler.


class BridgeError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeError("Routes redirects are not allowed")


def validate_endpoint(endpoint):
    if not isinstance(endpoint, dict):
        raise BridgeError("Endpoint must be a JSON object")
    if endpoint.get("revit_version") != "2021":
        raise BridgeError("Endpoint is not registered for Revit 2021")
    parts = urlsplit(endpoint.get("url", ""))
    if (parts.scheme != "http" or parts.hostname != "127.0.0.1"
            or not parts.port or parts.path != "/mcp-health/health"
            or parts.username or parts.password or parts.query or parts.fragment):
        raise BridgeError("Expected loopback HTTP /mcp-health/health endpoint")
    if type(endpoint.get("process_id")) is not int or endpoint["process_id"] <= 0:
        raise BridgeError("Endpoint process_id is invalid")
    return endpoint


def get_health(endpoint, timeout=30):
    validate_endpoint(endpoint)
    # Ignore Windows/user proxy settings for local process communication.
    opener = build_opener(ProxyHandler({}), NoRedirects())
    try:
        with opener.open(endpoint["url"], timeout=timeout) as response:
            if response.geturl() != endpoint["url"]:
                raise BridgeError("Unexpected redirect from Routes")
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise BridgeError("Routes response exceeds 1 MiB")
            data = json.loads(raw)
    except (HTTPError, URLError, socket.timeout, TimeoutError, ValueError) as error:
        raise BridgeError("Routes unavailable or invalid response: {0}".format(error)) from error
    if not isinstance(data, dict):
        raise BridgeError("Expected a JSON object from Routes")
    if data.get("success") is not True:
        raise BridgeError("Revit health failed: {0}".format(data))
    expected = {"revit_version": "2021", "revit_api_version": "21.0.0.0",
                "revit_apiui_version": "21.0.0.0", "routes_available": True,
                "process_id": endpoint["process_id"]}
    for key, value in expected.items():
        if data.get(key) != value:
            raise BridgeError("Revit identity mismatch: {0}".format(key))
    if not str(data.get("pyrevit_version", "")).startswith("5.1."):
        raise BridgeError("Expected pyRevit 5.1.x")
    if not data.get("revit_build") or not data.get("checked_at_utc"):
        raise BridgeError("Missing live Revit build/timestamp")
    return data


def _select_instance(runtime_dir=None):
    runtime_dir = Path(runtime_dir or ROOT / ".runtime")
    target_pid = os.environ.get("REVIT_PROCESS_ID")
    candidates = sorted(runtime_dir.glob("revit2021-*.json"))
    if target_pid:
        if not target_pid.isdecimal():
            raise BridgeError("REVIT_PROCESS_ID must be a positive process id")
        candidates = [runtime_dir / ("revit2021-" + target_pid + ".json")]
    if not candidates:
        raise BridgeError("No Revit 2021 endpoint. Load MCPHealth.extension in Revit 2021.")
    results, failures = [], []
    for path in candidates:
        try:
            endpoint = json.loads(path.read_text(encoding="utf-8"))
            results.append((endpoint, get_health(endpoint)))
        except (OSError, ValueError, BridgeError) as error:
            failures.append("{0}: {1}".format(path.name, error))
    if len(results) > 1:
        raise BridgeError("Multiple Revit 2021 instances. Set REVIT_PROCESS_ID explicitly.")
    if not results:
        raise BridgeError("; ".join(failures))
    return results[0]


def revit_health_check(runtime_dir=None):
    with _lock:
        _, result = _select_instance(runtime_dir)
        result["mcp_available"] = True
        result["mcp_transport"] = "stdio"
        result["mcp_implementation"] = "local MCP bridge to pyRevit Routes"
        return result


def validate_roundtrip_result(data, endpoint, request_id):
    """Reject false positives; preserve the unmodified Revit JSON for the caller."""
    if not isinstance(data, dict):
        raise BridgeError("Expected a JSON object from the family test")
    if data.get("request_id") != request_id or data.get("process_id") != endpoint["process_id"]:
        raise BridgeError("Family test response identity mismatch")
    if data.get("success") is False:
        return  # Revit diagnostic failures are returned intact, not hidden behind HTTP 200.
    for key in ("success", "saved", "closed", "reopened", "verified",
                "closed_after_readback", "transaction_committed", "template_unchanged"):
        if data.get(key) is not True:
            raise BridgeError("Incomplete family test: " + key)
    if (data.get("test") != "family_parameter_roundtrip" or data.get("stage") != "complete"
            or data.get("revit_version") != "2021" or data.get("revit_api_version") != "21.0.0.0"
            or data.get("revit_apiui_version") != "21.0.0.0" or data.get("errors") != []):
        raise BridgeError("Family test metadata or errors are invalid")
    parameter = data.get("parameter", {})
    if (parameter.get("name") != "MCP_Ширина" or parameter.get("parameter_type") != "Length"
            or parameter.get("is_instance") is not False
            or parameter.get("parameter_group") != "PG_GEOMETRY"
            or data.get("family_type") != "MCP_TEST_1000"):
        raise BridgeError("Wrong family parameter or type after reopening")
    for key in ("write_value_mm", "value_before_save_mm", "readback_value_mm", "delta_mm"):
        value = data.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise BridgeError("Invalid numeric result: " + key)
    actual_delta = abs(data["readback_value_mm"] - 1000.0)
    if (data["write_value_mm"] != 1000.0 or abs(data["value_before_save_mm"] - 1000.0) > 0.001
            or actual_delta > 0.001 or abs(data["delta_mm"] - actual_delta) > 1e-9):
        raise BridgeError("Family test readback is outside tolerance")
    expected_path = (ROOT / "tests" / "output" / request_id / "MCP_Test_Parameter_Roundtrip.rfa").resolve()
    output = Path(data.get("output_path", "")).resolve()
    if output != expected_path or not output.is_file() or output.stat().st_size == 0:
        raise BridgeError("Saved RFA is missing or outside this test's output directory")


def revit_test_family_parameter_roundtrip(runtime_dir=None):
    """One POST, no retries. A timeout means the outcome is unknown, not that no file was saved."""
    with _lock:
        endpoint, health = _select_instance(runtime_dir)
        path = "/family-mcp/test-parameter-roundtrip"
        if not any(route.get("path") == path and route.get("method") == "POST"
                   for route in health.get("registered_routes", [])):
            raise BridgeError("Family test route is not loaded. Reload pyRevit in Revit 2021.")
        request_id = str(uuid.uuid4())
        payload = {"request_id": request_id, "expected_process_id": endpoint["process_id"],
                   "write_value_mm": 1000.0}
        parts = urlsplit(endpoint["url"])
        url = "http://" + parts.netloc + path
        request = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                          headers={"Content-Type": "application/json"})
        opener = build_opener(ProxyHandler({}), NoRedirects())
        try:
            with opener.open(request, timeout=120) as response:
                raw = response.read(1024 * 1024 + 1)
                if response.geturl() != url or len(raw) > 1024 * 1024:
                    raise BridgeError("Invalid family test HTTP response")
                data = json.loads(raw)
        except (HTTPError, URLError, socket.timeout, TimeoutError, ValueError) as error:
            raise BridgeError("Family test response unavailable; outcome may be unknown. "
                              "Do not retry automatically. Inspect tests/output/{0}. Details: {1}".format(
                                  request_id, error)) from error
        reports = ROOT / "reports"
        reports.mkdir(exist_ok=True)
        # Save the actual response even if semantic validation fails.
        (reports / "codex_family_parameter_roundtrip.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        validate_roundtrip_result(data, endpoint, request_id)
        return data


def active_family_call(operation, data=None):
    allowed = {"context", "inspect", "preflight", "set-parameters", "build", "test-geometry", "detail", "presentation"}
    if operation not in allowed:
        raise BridgeError("Unsupported active family operation")
    with _lock:
        endpoint, health = _select_instance()
        if not health.get("has_active_document") or not health.get("is_family_document"):
            raise BridgeError("Open the intended RFA manually in Revit 2021 before this operation")
        request_id = str(uuid.uuid4())
        payload = {"request_id": request_id, "expected_process_id": endpoint["process_id"], "data": data or {}}
        url = "http://" + urlsplit(endpoint["url"]).netloc + "/family-mcp/active/" + operation
        request = Request(url, data=json.dumps(payload, allow_nan=False).encode("utf-8"), method="POST",
                          headers={"Content-Type": "application/json"})
        try:
            with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=120) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise BridgeError("Family report exceeded 16 MiB")
                result = json.loads(raw)
                if isinstance(result, dict) and set(result) == {"payload_json"}:
                    result = json.loads(result["payload_json"])
        except (HTTPError, URLError, socket.timeout, TimeoutError, ValueError) as error:
            detail = str(error)
            if isinstance(error, HTTPError):
                detail += ": " + error.read(16384).decode("utf-8", errors="replace")
            raise BridgeError("Active family request failed; outcome may be unknown. Do not retry writes "
                              "automatically. Request {0}: {1}".format(request_id, detail)) from error
        if not isinstance(result, dict) or result.get("process_id") != endpoint["process_id"] or result.get("request_id") != request_id:
            raise BridgeError("Active family response identity mismatch")
        reports = ROOT / "reports"
        reports.mkdir(exist_ok=True)
        filename = ("codex_active_family_geometry_test.json" if operation == "test-geometry" and
                    not (data or {}).get("dry_run", True) else "active_family_" + operation + ".json")
        (reports / filename).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
