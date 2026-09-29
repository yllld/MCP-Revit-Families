"""Real MCP initialize -> tools/list -> tools/call -> live Revit; no mocks."""
import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "mcp_server.server"], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session:
            init = await session.initialize()
            listing = await session.list_tools()
            assert "revit_health_check" in [tool.name for tool in listing.tools]
            result = await session.call_tool("revit_health_check", {})
            if result.isError:
                raise RuntimeError(result.model_dump_json())
            health = json.loads(next(item.text for item in result.content if item.type == "text"))
            assert health["success"] is True
            assert health["revit_version"] == "2021"
            assert health["revit_api_version"] == "21.0.0.0"
            report = {
                "test": "MCP SDK client -> stdio server -> pyRevit Routes -> live Revit 2021",
                "native_codex_tool_call": False,
                "tested_at_utc": datetime.now(timezone.utc).isoformat(),
                "protocol_version": init.protocolVersion,
                "server": init.serverInfo.model_dump(),
                "tools": [tool.name for tool in listing.tools],
                "health": health,
            }
            reports = ROOT / "reports"
            reports.mkdir(exist_ok=True)
            (reports / "health_check.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
