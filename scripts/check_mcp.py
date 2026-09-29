"""Check real MCP initialize/tools/list without connecting to Revit or writing a model."""
import asyncio
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    launcher = Path(__file__).resolve().with_name('run_server.py')
    params = StdioServerParameters(command=sys.executable, args=[str(launcher)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            listing = await session.list_tools()
            names = sorted(tool.name for tool in listing.tools)
            expected = {'revit_health_check', 'revit_get_active_family_context',
                        'revit_inspect_active_family', 'revit_family_preflight',
                        'revit_set_existing_family_parameters',
                        'revit_build_equipment_in_active_family',
                        'revit_test_geometry_in_active_family', 'revit_add_family_details',
                        'revit_finish_detail_presentation'}
            if set(names) != expected:
                raise RuntimeError('Unexpected MCP tool list: ' + repr(names))
            print(json.dumps({'success': True, 'revit_contacted': False,
                              'server': init.serverInfo.name, 'tools': names}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
