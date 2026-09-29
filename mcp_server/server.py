"""Run with: .venv/Scripts/python.exe -m mcp_server.server"""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp_server import bridge

mcp = FastMCP("revit-family-mcp")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False))
def revit_health_check() -> dict:
    """Read live Revit 2021 build, API assemblies, pyRevit engine and registered routes.

    Runs in Revit's API context. Works without an open document. Does not modify
    documents. Rejects Revit versions other than 2021 and mismatched process IDs.
    """
    return bridge.revit_health_check()


READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


@mcp.tool(annotations=READ)
def revit_get_active_family_context() -> dict:
    """Validate the manually opened Revit 2021 FamilyDocument; no model changes."""
    return bridge.active_family_call("context")


@mcp.tool(annotations=READ)
def revit_inspect_active_family() -> dict:
    """Read ALL family parameters, GUIDs, formulas, values, types, geometry and connectors.
    No transactions. Returns a context_token identifying the audited active family state.
    """
    return bridge.active_family_call("inspect")


@mcp.tool(annotations=READ)
def revit_family_preflight() -> dict:
    """Read-only preflight including existing geometry, reference planes and coordinate system."""
    return bridge.active_family_call("preflight")


@mcp.tool(annotations=WRITE)
def revit_set_existing_family_parameters(values: dict, dry_run: bool = True, context_token: str = "") -> dict:
    """Plan/write exact existing family parameters with explicit units and source traceability.
    Default is dry-run. Writes require the latest audit context_token and a backup.
    Never creates ADSK/shared parameters, alters formulas or edits connectors.
    ADSK/shared values require direct model-matched evidence and confidence >= 0.95.
    Review/unknown/protected values are reported and skipped, not written.
    Instance defaults require explicit write_instance_defaults=true in the values payload;
    their instance/type status is never changed and original types are preserved.
    Payload: {family_type, values:{name:{value,unit,source}}, parameter_mapping:{mappings,unmapped}}.
    """
    return bridge.active_family_call("set-parameters", {"values": values, "dry_run": dry_run,
                                                       "context_token": context_token})


@mcp.tool(annotations=WRITE)
def revit_build_equipment_in_active_family(spec: dict, dry_run: bool = True, context_token: str = "") -> dict:
    """Plan/build simplified equipment in the active family. Default dry-run; no RFT creation.
    Requires explicit geometry, parameter mapping and provenance. Reports unsupported operations
    instead of approximating them. Write requires the latest preflight context_token.
    May create ordinary geometry family_parameters (Length/Angle/YesNo), never shared/ADSK.
    Manufacturer source.designation takes priority over default L/B/H, d1... notation.
    New arithmetic formulas require explicit derived source.calculation; existing formulas are preserved.
    """
    return bridge.active_family_call("build", {"spec": spec, "dry_run": dry_run, "context_token": context_token})


@mcp.tool(annotations=WRITE)
def revit_test_geometry_in_active_family(dry_run: bool = True, context_token: str = "") -> dict:
    """Test a parametric box in the manually opened RFA; only this test may add 3 MCP Length parameters.
    Dry-run by default. Creates a backup and separate result RFA, uses TransactionGroup rollback,
    flexes 800x600x1200 -> 1000x800x1500 -> 600x400x900 mm, preserves existing family structure
    and connectors. Requires context_token for write. Does not close the active document.
    """
    return bridge.active_family_call("test-geometry", {"dry_run": dry_run, "context_token": context_token})


@mcp.tool(annotations=WRITE)
def revit_add_family_details(spec: dict, dry_run: bool = True, context_token: str = "") -> dict:
    """Add source-traceable details to a supported, previously generated assembly.
    Profiles: Airhorse BPM-40A (default) and WOS-8 drawing 3400401
    (spec.profile='wos8_drawing_3400401'). WOS finish uses operation='finish_wos8'.
    These are bounded product-specific adapters, not an arbitrary geometry endpoint.
    Default dry-run. Explicit straight-tank refinement retains original total tank length.
    Preserves ADSK, connectors and unrelated geometry; backs up and saves a separate RFA.
    """
    return bridge.active_family_call("detail", {"spec": spec, "dry_run": dry_run, "context_token": context_token})


@mcp.tool(annotations=WRITE)
def revit_finish_detail_presentation(dry_run: bool = True, context_token: str = "") -> dict:
    """Set thin projection lines and scale 1:20 on the generated Airhorse detail view.
    Backup, verify unchanged model/parameters, save separate RFA, export PNG.
    """
    return bridge.active_family_call("presentation", {"dry_run": dry_run, "context_token": context_token})


if __name__ == "__main__":
    mcp.run(transport="stdio")
