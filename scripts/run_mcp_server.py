"""
scripts/run_mcp_server.py

Convenience launcher for the History Forge MCP server.

Usage:
    python scripts/run_mcp_server.py

Equivalent to:
    python -m src.mcp.server
"""
import asyncio
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.mcp.server import main

if __name__ == "__main__":
    asyncio.run(main())
