"""Absolute-path launcher for Codex; independent of its working directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp_server.server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
