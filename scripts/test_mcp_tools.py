"""
scripts/test_mcp_tools.py

Headless smoke test for MCP tools. Calls tool functions directly
without going through the MCP protocol layer. Use this to verify
the full pipeline works in a headless context before testing
via Claude Code.

Usage:
    python scripts/test_mcp_tools.py
"""
import asyncio
import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.mcp.tools import run_daily_short_video, generate_topic, get_recent_daily_runs

async def main():
    print("=== Smoke Test: generate_topic ===")
    result = await generate_topic({})
    print(json.dumps(json.loads(result[0].text), indent=2))

    print("\n=== Smoke Test: get_recent_daily_runs ===")
    result = await get_recent_daily_runs({"limit": 3})
    print(json.dumps(json.loads(result[0].text), indent=2))

    print("\n=== Smoke Test: run_daily_short_video (with explicit topic) ===")
    result = await run_daily_short_video({"topic": "The Black Death, 1347"})
    print(json.dumps(json.loads(result[0].text), indent=2))

asyncio.run(main())
