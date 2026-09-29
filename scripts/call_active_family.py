"""Explicit MCP client; defaults to a read-only audit. Never retries a write."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    tool = sys.argv[1] if len(sys.argv) > 1 else "revit_inspect_active_family"
    arguments = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig")) if len(sys.argv) > 2 and sys.argv[2] != "--summary" else {}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "scripts/run_server.py")])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=180)) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            if result.isError:
                print(result.model_dump_json())
                return 1
            data = json.loads(next(item.text for item in result.content if item.type == "text"))
            display = data
            if "--summary" in sys.argv:
                fields = ("success", "ready", "dry_run", "stage", "errors", "warnings", "error", "traceback",
                          "context_token", "process_id", "request_id", "saved", "model_committed",
                          "transaction_group_rolled_back", "active_rfa_path_after", "log_path", "preview",
                          "assembly_validation", "validation", "parameter_count_before", "parameter_count_after",
                          "connector_count_before", "connector_count_after", "types_to_create")
                display = {key: data[key] for key in fields if key in data}
                for key in ("geometry_to_create", "parameters_to_create", "adsk_parameters_to_write", "flex_test"):
                    if key in data:
                        display[key + "_count"] = len(data[key])
            print(json.dumps(display, ensure_ascii=True, indent=2))
            return 0 if data.get("success") and data.get("ready", True) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
