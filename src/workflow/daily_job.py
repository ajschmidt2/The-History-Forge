"""Headless daily automation job for generating a complete short video."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_openai_config
from src.constants import SUPABASE_VIDEO_BUCKET
from src.storage import upsert_project
import src.supabase_storage as _sb_store
from src.topics.daily_topics import generate_daily_topic, load_used_topics, save_used_topic
from src.workflow.presets import DAILY_SHORT_PRESET, DailyShortPreset
from src.workflow.project_io import ensure_project_files, load_project_payload, project_dir, save_project_payload
from src.workflow.services import FullWorkflowOptions, run_full_workflow

RUN_HISTORY_PATH = Path("data/daily_run_history.json")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _project_id_for_day(run_date: date) -> str:
    return f"daily_{run_date.isoformat().replace('-', '_')}"


def _load_run_history(path: Path = RUN_HISTORY_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _append_run_history(entry: dict[str, Any], path: Path = RUN_HISTORY_PATH) -> None:
    rows = _load_run_history(path)
    rows.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows[-1000:], indent=2), encoding="utf-8")


def _resolve_default_music_track() -> str:
    library = Path("data/music_library")
    if not library.exists():
        return ""
    candidates = sorted([p for p in library.glob("*.*") if p.suffix.lower() in {".mp3", ".wav", ".m4a"}], key=lambda p: p.name.lower())
    return str(candidates[0]) if candidates else ""


def generate_daily_short_script(topic: str, preset: DailyShortPreset = DAILY_SHORT_PRESET) -> str:
    config = get_openai_config()
    api_key = str(config.get("api_key") or "").strip()
    model = str(config.get("model") or "gpt-4o-mini").strip()
    if not api_key:
        raise RuntimeError("OpenAI API key is required for the daily short script generator.")

    from openai import OpenAI

    prompt = (
        f"Write a voiceover-only history script for this topic: {topic}. "
        f"Target about {preset.target_word_count} words and about {preset.target_duration_seconds} seconds when narrated. "
        "No markdown. No bullets. No scene labels. No visual notes. "
        "Use a strong hook, concise storytelling, and high retention pacing. "
        f"End with a clear call to action: {preset.last_scene_cta_text}"
    )
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        temperature=0.7,
        messages=[
            {"role": "system", "content": "You write accurate, engaging short-form history voiceover scripts."},
            {"role": "user", "content": prompt},
        ],
    )
    return str(resp.choices[0].message.content or "").strip()


def _upload_final_to_generated_bucket(project_id: str, final_path: Path, run_date: date) -> dict[str, str]:
    if not final_path.exists() or final_path.stat().st_size <= 0:
        raise RuntimeError(f"Final render not found at {final_path}")
    object_path = f"daily-renders/{run_date.isoformat()}/{project_id}_{final_path.name}"
    public_url = _sb_store.upload_video_bytes(
        bucket=SUPABASE_VIDEO_BUCKET,
        storage_path=object_path,
        video_bytes=final_path.read_bytes(),
    )
    if not public_url:
        raise RuntimeError(f"Failed to upload render to Supabase bucket '{SUPABASE_VIDEO_BUCKET}'.")
    return {"bucket": SUPABASE_VIDEO_BUCKET, "object_path": object_path, "public_url": public_url}


def run_daily_video_job(run_date: date | None = None) -> dict[str, Any]:
    target_date = run_date or date.today()
    project_id = _project_id_for_day(target_date)
    timestamp = _utc_now().isoformat()

    used_topics = load_used_topics()
    topic = generate_daily_topic(used_topics=used_topics)
    script_text = generate_daily_short_script(topic, DAILY_SHORT_PRESET)
    music_track = _resolve_default_music_track()
    if not music_track:
        raise RuntimeError("No background music track found. Add at least one file to data/music_library.")

    ensure_project_files(project_id)
    upsert_project(project_id, f"Daily Video {target_date.isoformat()}")

    payload = load_project_payload(project_id)
    payload.update(
        {
            "title": f"Daily Video {target_date.isoformat()}",
            "topic": topic,
            "topic_direction": "",
            "script_text": script_text,
            "script_profile": "youtube_short_60s",
            "automation_mode": "existing_script_full_workflow",
            "aspect_ratio": DAILY_SHORT_PRESET.aspect_ratio,
            "output_width": DAILY_SHORT_PRESET.output_width,
            "output_height": DAILY_SHORT_PRESET.output_height,
            "scene_count": DAILY_SHORT_PRESET.scene_count,
            "max_scenes": DAILY_SHORT_PRESET.scene_count,
            "visual_style": DAILY_SHORT_PRESET.visual_style,
            "enable_video_effects": True,
            "video_effects_style": DAILY_SHORT_PRESET.effects_style,
            "enable_subtitles": False,
            "automation_include_captions": False,
            "burn_subtitles": False,
            "generate_srt": False,
            "enable_music": True,
            "music_volume_relative_to_voiceover": DAILY_SHORT_PRESET.music_relative_level,
            "selected_music_track": music_track,
            "tts_provider": DAILY_SHORT_PRESET.voice_provider,
            "openai_tts_model": DAILY_SHORT_PRESET.openai_tts_model,
            "openai_tts_voice": DAILY_SHORT_PRESET.openai_tts_voice,
            "daily_preset": DAILY_SHORT_PRESET.as_dict(),
            "daily_job_run_date": target_date.isoformat(),
            "daily_job_started_at": timestamp,
        }
    )
    save_project_payload(project_id, payload)
    (project_dir(project_id) / "script.txt").write_text(script_text, encoding="utf-8")

    pipeline = DAILY_SHORT_PRESET.to_pipeline_options(topic=topic, selected_music_track=music_track)
    run_result = run_full_workflow(
        project_id,
        FullWorkflowOptions(
            mode="full_auto",
            overwrite_script=False,
            overwrite_scenes=True,
            overwrite_prompts=True,
            overwrite_images=True,
            overwrite_voiceover=True,
            overwrite_timeline=True,
            overwrite_render=True,
            pipeline=pipeline,
        ),
    )

    final_path = Path(run_result.final_output_path or project_dir(project_id) / "renders/final.mp4")
    if run_result.failed_step:
        error = f"Workflow failed at step '{run_result.failed_step}': {'; '.join(run_result.warnings)}"
        history = {
            "timestamp": timestamp,
            "date": target_date.isoformat(),
            "project_id": project_id,
            "topic": topic,
            "status": "failed",
            "final_render_path": str(final_path),
            "bucket_path": "",
            "error": error,
        }
        _append_run_history(history)
        raise RuntimeError(error)

    upload_result = _upload_final_to_generated_bucket(project_id, final_path, target_date)
    save_used_topic(topic, run_date=target_date)

    summary = {
        "timestamp": timestamp,
        "date": target_date.isoformat(),
        "project_id": project_id,
        "topic": topic,
        "status": "success",
        "final_render_path": str(final_path),
        "bucket": upload_result["bucket"],
        "bucket_path": upload_result["object_path"],
        "public_url": upload_result["public_url"],
        "subtitles_enabled": False,
        "tts_provider": DAILY_SHORT_PRESET.voice_provider,
        "openai_tts_model": DAILY_SHORT_PRESET.openai_tts_model,
        "openai_tts_voice": DAILY_SHORT_PRESET.openai_tts_voice,
        "music_track": music_track,
        "music_relative_level": DAILY_SHORT_PRESET.music_relative_level,
        "scene_count": DAILY_SHORT_PRESET.scene_count,
    }
    _append_run_history(summary)

    payload = load_project_payload(project_id)
    payload["daily_job_last_result"] = summary
    payload["daily_job_completed_at"] = _utc_now().isoformat()
    payload["generated_video_bucket_path"] = upload_result["object_path"]
    payload["generated_video_public_url"] = upload_result["public_url"]
    payload["enable_subtitles"] = False
    save_project_payload(project_id, payload)
    return summary


def _cli() -> int:
    try:
        result = run_daily_video_job()
    except Exception as exc:  # noqa: BLE001
        print(f"DAILY_JOB_FAILED: {exc}", file=sys.stderr)
        return 1

    print("DAILY_JOB_SUCCESS")
    print(f"project_id={result['project_id']}")
    print(f"topic={result['topic']}")
    print(f"final_render_path={result['final_render_path']}")
    print(f"bucket_path={result['bucket_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
