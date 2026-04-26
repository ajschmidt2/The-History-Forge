"""
src/mcp/tools.py

Async tool handler functions for the History Forge MCP server.

Each handler accepts a raw dict of arguments and returns
[mcp.types.TextContent(type="text", text=json.dumps(result_dict))].
All exceptions are caught and returned as structured error responses.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.types import TextContent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP default preset — used when run_daily_short_video inputs are not provided.
# These are the canonical MCP-triggered video defaults.
# ---------------------------------------------------------------------------
MCP_DEFAULT_PRESET: dict[str, Any] = {
    "aspect_ratio": "9:16",
    "visual_style": "Dramatic illustration",
    "effects_style": "Ken Burns - Strong",
    "voice_provider": "openai",
    "openai_tts_model": "gpt-4o-mini-tts",
    "openai_tts_voice": "ash",
    "target_word_count": 150,
    "target_duration_seconds": 60,
    "scene_count": 14,
    "subtitles_enabled": False,
    "music_enabled": True,
    "music_relative_level": 0.09,
    "cta_text": "Subscribe to History Crossroads",
    "ai_video_provider": "google_veo_lite",
    "scene_transition_type": "random",
    "selected_music_track": "data/music_library/History Track 60 Seconds - String instruments - 4.mp3",
}

# Preset keys that map directly to DailyShortPreset / settings["preset"] fields
_PRESET_OVERRIDE_KEYS = (
    "visual_style",
    "effects_style",
    "voice_provider",
    "openai_tts_model",
    "openai_tts_voice",
    "target_word_count",
    "target_duration_seconds",
    "scene_count",
    "subtitles_enabled",
    "music_enabled",
    "music_relative_level",
    "ai_video_provider",
    "scene_transition_type",
)


def _load_run_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_run_history(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows[-1000:], indent=2), encoding="utf-8")


def _patch_last_run_history_entry(updates: dict[str, Any], path: Path) -> None:
    """Update the last entry in the run history JSON with additional fields."""
    rows = _load_run_history(path)
    if rows:
        rows[-1].update(updates)
        _save_run_history(rows, path)


# ---------------------------------------------------------------------------
# Tool 1: run_daily_short_video
# ---------------------------------------------------------------------------

async def run_daily_short_video(arguments: dict[str, Any]) -> list[TextContent]:
    """
    Run the full History Forge short-video automation pipeline.
    Wraps run_daily_video_job() with MCP-specific defaults and trigger_source logging.
    """
    failed_step: str | None = None
    try:
        import importlib, sys as _sys
        # Force reload of pipeline modules so code changes on disk are picked up
        # without requiring an MCP server restart.
        for _mod_name in (
            "src.video.ai_video_clips",
            "src.video.ffmpeg_render",
            "src.workflow.services",
            "src.workflow.daily_job",
        ):
            if _mod_name in _sys.modules:
                try:
                    importlib.reload(_sys.modules[_mod_name])
                except Exception as _e:
                    logger.warning("mcp: reload of %s failed: %s", _mod_name, _e)

        from src.workflow.daily_job import (
            RUN_HISTORY_PATH,
            load_daily_automation_settings,
            save_daily_automation_settings,
            run_daily_video_job,
        )

        # Load current saved settings as base
        existing = load_daily_automation_settings()
        existing_preset = existing.get("preset") or {}

        # Build preset: start from existing, layer MCP defaults, then explicit inputs
        merged_preset: dict[str, Any] = {**existing_preset}

        # Apply MCP defaults for all preset keys (fills gaps)
        for key in _PRESET_OVERRIDE_KEYS:
            merged_preset[key] = MCP_DEFAULT_PRESET.get(key, merged_preset.get(key))

        # Override with any explicit tool inputs
        for key in _PRESET_OVERRIDE_KEYS:
            if key in arguments and arguments[key] is not None:
                merged_preset[key] = arguments[key]

        # Apply CTA text
        merged_preset["last_scene_cta_text"] = MCP_DEFAULT_PRESET["cta_text"]

        new_settings: dict[str, Any] = {
            "topic_override": arguments.get("topic") or "",
            "topic_direction": arguments.get("topic_direction") or existing.get("topic_direction", ""),
            "selected_music_track": (
                arguments.get("selected_music_track")
                or existing.get("selected_music_track", "")
                or MCP_DEFAULT_PRESET.get("selected_music_track", "")
            ),
            "preset": merged_preset,
        }

        save_daily_automation_settings(new_settings)
        logger.info("mcp run_daily_short_video: settings saved, starting job")

        summary = run_daily_video_job()

        # Tag both the in-memory summary and the persisted history entry
        summary["trigger_source"] = "mcp"
        _patch_last_run_history_entry({"trigger_source": "mcp"}, RUN_HISTORY_PATH)

        # Also log to the project's workflow log
        project_id = summary.get("project_id", "")
        if project_id:
            try:
                from src.workflow.services import _workflow_logger
                wf_logger = _workflow_logger(project_id)
                wf_logger.info("trigger_source=mcp topic=%s", summary.get("topic", ""))
            except Exception:  # noqa: BLE001
                pass

        logger.info(
            "mcp run_daily_short_video: success project_id=%s topic=%s",
            project_id,
            summary.get("topic"),
        )

        return [TextContent(type="text", text=json.dumps({
            "success": True,
            "project_id": summary.get("project_id"),
            "topic": summary.get("topic"),
            "final_render_path": summary.get("final_render_path"),
            "bucket_path": summary.get("bucket_path"),
            "public_url": summary.get("public_url"),
            "youtube_video_id": summary.get("youtube_video_id", ""),
            "youtube_url": summary.get("youtube_url", ""),
            "failed_step": None,
            "warnings": [],
            "error": None,
        }))]

    except Exception as exc:  # noqa: BLE001
        logger.error("mcp run_daily_short_video failed: %s", exc, exc_info=True)
        return [TextContent(type="text", text=json.dumps({
            "success": False,
            "project_id": None,
            "topic": None,
            "final_render_path": None,
            "bucket_path": None,
            "public_url": None,
            "youtube_video_id": None,
            "youtube_url": None,
            "failed_step": failed_step,
            "warnings": [],
            "error": str(exc),
        }))]


# ---------------------------------------------------------------------------
# Tool 2: generate_topic
# ---------------------------------------------------------------------------

async def generate_topic(arguments: dict[str, Any]) -> list[TextContent]:
    """Generate a history video topic using the existing daily_topics logic."""
    try:
        from src.topics.daily_topics import (
            generate_daily_topic,
            load_used_topics,
        )

        topic_direction = str(arguments.get("topic_direction") or "").strip()
        avoid_recent = bool(arguments.get("avoid_recent", True))

        used_topics = load_used_topics() if avoid_recent else set()
        topic = generate_daily_topic(used_topics=used_topics, topic_direction=topic_direction)

        # Determine source: if it came from OpenAI generation vs curated list
        from src.topics.daily_topics import CURATED_TOPICS
        source = "curated" if topic in CURATED_TOPICS else "openai"
        generated_or_fallback = topic not in CURATED_TOPICS

        logger.info("mcp generate_topic: topic=%r source=%s", topic, source)

        return [TextContent(type="text", text=json.dumps({
            "success": True,
            "topic": topic,
            "source": source,
            "generated_or_fallback": generated_or_fallback,
            "error": None,
        }))]

    except Exception as exc:  # noqa: BLE001
        logger.error("mcp generate_topic failed: %s", exc, exc_info=True)
        return [TextContent(type="text", text=json.dumps({
            "success": False,
            "topic": None,
            "source": None,
            "generated_or_fallback": None,
            "error": str(exc),
        }))]


# ---------------------------------------------------------------------------
# Tool 3: get_recent_daily_runs
# ---------------------------------------------------------------------------

async def get_recent_daily_runs(arguments: dict[str, Any]) -> list[TextContent]:
    """Read recent daily run history from data/daily_run_history.json."""
    try:
        from src.workflow.daily_job import RUN_HISTORY_PATH

        limit = int(arguments.get("limit") or 10)
        limit = max(1, min(limit, 200))

        rows = _load_run_history(RUN_HISTORY_PATH)
        recent = rows[-limit:] if rows else []

        # Normalize each entry to the expected output shape
        results = []
        for row in reversed(recent):  # newest first
            results.append({
                "date": row.get("date"),
                "topic": row.get("topic"),
                "status": row.get("status"),
                "project_id": row.get("project_id"),
                "final_render_path": row.get("final_render_path"),
                "bucket_path": row.get("bucket_path"),
                "public_url": row.get("public_url"),
                "youtube_video_id": row.get("youtube_video_id", ""),
                "youtube_url": row.get("youtube_url", ""),
                "trigger_source": row.get("trigger_source"),
            })

        logger.info("mcp get_recent_daily_runs: returning %d records", len(results))

        return [TextContent(type="text", text=json.dumps({
            "success": True,
            "runs": results,
            "total_in_history": len(rows),
            "error": None,
        }))]

    except Exception as exc:  # noqa: BLE001
        logger.error("mcp get_recent_daily_runs failed: %s", exc, exc_info=True)
        return [TextContent(type="text", text=json.dumps({
            "success": False,
            "runs": [],
            "total_in_history": 0,
            "error": str(exc),
        }))]


# ---------------------------------------------------------------------------
# Tool 4: rerun_project_render
# ---------------------------------------------------------------------------

async def rerun_project_render(arguments: dict[str, Any]) -> list[TextContent]:
    """Re-run the render step only for an existing completed project."""
    try:
        import importlib, sys as _sys
        for _mod_name in ("src.video.ffmpeg_render", "src.workflow.services"):
            if _mod_name in _sys.modules:
                try:
                    importlib.reload(_sys.modules[_mod_name])
                except Exception as _e:
                    logger.warning("mcp: reload of %s failed: %s", _mod_name, _e)

        from src.workflow.services import (
            FullWorkflowOptions,
            PipelineOptions,
            run_full_workflow,
        )
        from src.workflow.project_io import load_project_payload, project_dir

        project_id = str(arguments.get("project_id") or "").strip()
        if not project_id:
            return [TextContent(type="text", text=json.dumps({
                "success": False,
                "final_render_path": None,
                "warnings": [],
                "error": "project_id is required",
            }))]

        # Verify project exists
        proj_path = project_dir(project_id)
        if not proj_path.exists():
            return [TextContent(type="text", text=json.dumps({
                "success": False,
                "final_render_path": None,
                "warnings": [],
                "error": f"Project not found: {project_id}",
            }))]

        payload = load_project_payload(project_id)

        pipeline = PipelineOptions(
            aspect_ratio=str(payload.get("aspect_ratio", "9:16") or "9:16"),
            visual_style=str(payload.get("visual_style", "Dramatic illustration") or "Dramatic illustration"),
            enable_video_effects=bool(payload.get("enable_video_effects", True)),
            video_effects_style=str(payload.get("video_effects_style", "Ken Burns - Standard") or "Ken Burns - Standard"),
            include_subtitles=bool(payload.get("enable_subtitles", False)),
            include_music=bool(payload.get("enable_music", True)),
            selected_music_track=str(payload.get("selected_music_track", "") or ""),
            music_volume_relative_to_voiceover=float(payload.get("music_volume_relative_to_voiceover", 0.15) or 0.15),
            tts_provider=str(payload.get("tts_provider", "openai") or "openai"),
            openai_tts_model=str(payload.get("openai_tts_model", "gpt-4o-mini-tts") or "gpt-4o-mini-tts"),
            openai_tts_voice=str(payload.get("openai_tts_voice", "ash") or "ash"),
            number_of_scenes=int(payload.get("scene_count", 14) or 14),
            automation_mode="existing_script_full_workflow",
        )

        options = FullWorkflowOptions(
            mode="rerender_only",
            overwrite_render=True,
            pipeline=pipeline,
        )

        logger.info("mcp rerun_project_render: starting render for project_id=%s", project_id)
        run_result = run_full_workflow(project_id, options)

        if run_result.failed_step:
            return [TextContent(type="text", text=json.dumps({
                "success": False,
                "final_render_path": None,
                "warnings": run_result.warnings,
                "error": f"Render failed at step '{run_result.failed_step}': {'; '.join(run_result.warnings)}",
            }))]

        logger.info(
            "mcp rerun_project_render: success project_id=%s render=%s",
            project_id,
            run_result.final_output_path,
        )

        return [TextContent(type="text", text=json.dumps({
            "success": True,
            "final_render_path": run_result.final_output_path,
            "warnings": run_result.warnings,
            "error": None,
        }))]

    except Exception as exc:  # noqa: BLE001
        logger.error("mcp rerun_project_render failed: %s", exc, exc_info=True)
        return [TextContent(type="text", text=json.dumps({
            "success": False,
            "final_render_path": None,
            "warnings": [],
            "error": str(exc),
        }))]


async def upload_project_video(arguments: dict[str, Any]) -> list[TextContent]:
    """Upload an existing rendered project to Supabase and YouTube."""
    try:
        from datetime import date, datetime, timezone
        from src.config.secrets import get_secret
        from src.constants import SUPABASE_VIDEO_BUCKET
        from src.services.youtube_upload import upload_video as _yt_upload_video
        from src.workflow.daily_job import _append_run_history, _upload_final_to_generated_bucket
        from src.workflow.project_io import load_project_payload, project_dir, save_project_payload
        import src.supabase_storage as _sb_store

        project_id = str(arguments.get("project_id") or "").strip()
        if not project_id:
            return [TextContent(type="text", text=json.dumps({
                "success": False, "error": "project_id is required",
            }))]

        proj_path = project_dir(project_id)
        if not proj_path.exists():
            return [TextContent(type="text", text=json.dumps({
                "success": False, "error": f"Project not found: {project_id}",
            }))]

        final_path = proj_path / "renders" / "final.mp4"
        if not final_path.exists():
            return [TextContent(type="text", text=json.dumps({
                "success": False, "error": f"Render not found: {final_path}",
            }))]

        payload = load_project_payload(project_id)
        topic = str(payload.get("topic", "") or payload.get("project_title", project_id) or project_id)
        run_date = date.today()

        # Supabase upload
        logger.info("mcp upload_project_video: uploading to Supabase project_id=%s", project_id)
        upload_result = _upload_final_to_generated_bucket(project_id, final_path, run_date)
        logger.info("mcp upload_project_video: Supabase upload complete public_url=%s", upload_result["public_url"])

        # YouTube upload
        youtube_video_id = ""
        youtube_url = ""
        _yt_client_secrets = Path(get_secret("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")).expanduser()
        _yt_token = Path(get_secret("YOUTUBE_TOKEN_FILE", "token.json")).expanduser()
        if _yt_client_secrets.exists() and _yt_token.exists():
            try:
                _yt_title = f"{topic} #shorts #history"
                _yt_description = f"{topic}\n\nSubscribe to History Crossroads for more!"
                _yt_tags = [w.lower() for w in topic.split() if w.isalpha()] + ["history", "shorts", "historycrossroads"]
                _yt_result = _yt_upload_video(
                    video_path=final_path,
                    title=_yt_title,
                    description=_yt_description,
                    tags=_yt_tags,
                    category_id="27",
                    privacy_status="private",
                    client_secrets_file=_yt_client_secrets,
                    token_file=_yt_token,
                )
                youtube_video_id = _yt_result.video_id
                youtube_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
                logger.info("mcp upload_project_video: YouTube upload complete video_id=%s", youtube_video_id)
            except Exception as yt_exc:
                logger.warning("mcp upload_project_video: YouTube upload failed (non-fatal): %s", yt_exc)
                youtube_url = f"youtube_upload_failed: {yt_exc}"
        else:
            logger.info("mcp upload_project_video: YouTube credentials not found — skipping")

        # Update project payload and run history
        payload["generated_video_bucket_path"] = upload_result["object_path"]
        payload["generated_video_public_url"] = upload_result["public_url"]
        payload["youtube_video_id"] = youtube_video_id
        payload["youtube_url"] = youtube_url
        save_project_payload(project_id, payload)

        _append_run_history({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "date": run_date.isoformat(),
            "project_id": project_id,
            "topic": topic,
            "status": "success",
            "final_render_path": str(final_path),
            "bucket_path": upload_result["object_path"],
            "public_url": upload_result["public_url"],
            "youtube_video_id": youtube_video_id,
            "youtube_url": youtube_url,
        })

        return [TextContent(type="text", text=json.dumps({
            "success": True,
            "project_id": project_id,
            "topic": topic,
            "public_url": upload_result["public_url"],
            "bucket_path": upload_result["object_path"],
            "youtube_video_id": youtube_video_id,
            "youtube_url": youtube_url,
            "error": None,
        }))]

    except Exception as exc:  # noqa: BLE001
        logger.error("mcp upload_project_video failed: %s", exc, exc_info=True)
        return [TextContent(type="text", text=json.dumps({
            "success": False, "error": str(exc),
        }))]
