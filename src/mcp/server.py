"""
src/mcp/server.py

MCP server entrypoint for The History Forge.
Exposes the video automation workflow as MCP tools over stdio transport.

Start with:
    python -m src.mcp.server
"""
from __future__ import annotations

import asyncio
import logging

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from src.mcp.tools import (
    generate_topic,
    get_recent_daily_runs,
    rerun_project_render,
    run_daily_short_video,
    upload_project_video,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Server("history-forge")

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[types.Tool] = [
    types.Tool(
        name="run_daily_short_video",
        description=(
            "Run the full History Forge short-video automation pipeline. "
            "Generates a topic (or uses the provided one), writes a ~60-second script, "
            "produces voiceover + images + effects, renders the final MP4, "
            "and uploads it to the Supabase generated-videos bucket. "
            "All inputs are optional; MCP defaults are applied when omitted."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "History topic for the video. Auto-generated if omitted.",
                },
                "topic_direction": {
                    "type": "string",
                    "description": "Optional focus direction hint for topic generation.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Video aspect ratio, e.g. '9:16' or '16:9'. Default: 9:16.",
                },
                "visual_style": {
                    "type": "string",
                    "description": "Image visual style. Default: 'Dramatic illustration'.",
                },
                "scene_count": {
                    "type": "integer",
                    "description": "Number of scenes. Default: 14.",
                },
                "effects_style": {
                    "type": "string",
                    "description": "Video effects style. Default: 'Ken Burns - Standard'.",
                },
                "voice_provider": {
                    "type": "string",
                    "description": "TTS provider: 'openai' or 'elevenlabs'. Default: openai.",
                },
                "openai_tts_model": {
                    "type": "string",
                    "description": "OpenAI TTS model. Default: 'gpt-4o-mini-tts'.",
                },
                "openai_tts_voice": {
                    "type": "string",
                    "description": "OpenAI TTS voice name. Default: 'ash'.",
                },
                "music_enabled": {
                    "type": "boolean",
                    "description": "Enable background music. Default: true.",
                },
                "music_relative_level": {
                    "type": "number",
                    "description": "Music volume relative to voiceover (0.0–1.0). Default: 0.15.",
                },
                "selected_music_track": {
                    "type": "string",
                    "description": "Path to a specific music track file. Uses library default if omitted.",
                },
                "subtitles_enabled": {
                    "type": "boolean",
                    "description": "Burn subtitles into video. Default: false.",
                },
                "target_word_count": {
                    "type": "integer",
                    "description": "Target script word count. Default: 150.",
                },
                "target_duration_seconds": {
                    "type": "integer",
                    "description": "Target video duration in seconds. Default: 60.",
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="generate_topic",
        description=(
            "Generate a single high-retention history video topic using the existing "
            "topic generation logic (OpenAI with curated fallback). "
            "Avoids recently used topics by default."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "topic_direction": {
                    "type": "string",
                    "description": "Optional focus direction for the generated topic.",
                },
                "avoid_recent": {
                    "type": "boolean",
                    "description": "Skip recently used topics. Default: true.",
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="get_recent_daily_runs",
        description=(
            "Return recent daily run history records from data/daily_run_history.json. "
            "Each record contains date, topic, status, project_id, render path, "
            "bucket path, public URL, and trigger_source."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recent records to return. Default: 10.",
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="rerun_project_render",
        description=(
            "Re-run the render step only for an existing completed project, "
            "without regenerating images, voiceover, or other assets. "
            "Reads render settings from the saved project payload."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "The project ID to re-render (required).",
                },
            },
            "required": ["project_id"],
        },
    ),
    types.Tool(
        name="upload_project_video",
        description=(
            "Upload an existing rendered project video to Supabase storage and YouTube. "
            "Use this when a render completed successfully but upload was skipped or failed. "
            "Updates the project payload and appends to run history."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "The project ID whose final.mp4 should be uploaded (required).",
                },
            },
            "required": ["project_id"],
        },
    ),
]

_TOOL_HANDLERS = {
    "run_daily_short_video": run_daily_short_video,
    "generate_topic": generate_topic,
    "get_recent_daily_runs": get_recent_daily_runs,
    "rerun_project_render": rerun_project_render,
    "upload_project_video": upload_project_video,
}


# ---------------------------------------------------------------------------
# MCP handler registration
# ---------------------------------------------------------------------------

@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return TOOL_SCHEMAS


@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    import json as _json

    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return [types.TextContent(
            type="text",
            text=_json.dumps({"success": False, "error": f"Unknown tool: {name}"}),
        )]
    return await handler(arguments or {})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("History Forge MCP server starting (stdio transport)")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
