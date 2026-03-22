"""Headless daily automation job for generating a complete short video."""

from __future__ import annotations

import json
import sys
import os
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_openai_config
from src.config.secrets import get_secret
from src.constants import SUPABASE_VIDEO_BUCKET
from src.services.youtube_upload import upload_video as _yt_upload_video
from src.storage import upsert_project
import src.supabase_storage as _sb_store
from src.topics.daily_topics import generate_daily_topic, load_used_topics, save_used_topic
from src.workflow.presets import DAILY_SHORT_PRESET, DailyShortPreset
from src.workflow.project_io import ensure_project_files, load_project_payload, project_dir, save_project_payload
from src.workflow.services import FullWorkflowOptions, run_full_workflow

RUN_HISTORY_PATH = Path("data/daily_run_history.json")
DAILY_AUTOMATION_SETTINGS_PATH = Path("data/daily_automation_settings.json")


# ---------------------------------------------------------------------------
# Channel profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChannelProfile:
    """All per-channel settings consumed by run_daily_video_job()."""
    channel_id: str
    channel_name: str
    topic_direction: str
    youtube_hashtags: list[str]
    youtube_subscribe_cta: str
    youtube_extra_tags: list[str]
    youtube_category_id: str
    youtube_client_secrets_secret: str   # key name passed to get_secret()
    youtube_token_file_secret: str       # key name passed to get_secret()
    automation_settings_path: Path
    topics_used_path: Path
    run_history_path: Path
    preset_overrides: dict = field(default_factory=dict)


HISTORY_CHANNEL = ChannelProfile(
    channel_id="history",
    channel_name="History Crossroads",
    topic_direction="",
    youtube_hashtags=["#shorts", "#history"],
    youtube_subscribe_cta="Subscribe to History Crossroads for more!",
    youtube_extra_tags=["history", "historycrossroads", "historyfacts"],
    youtube_category_id="27",
    youtube_client_secrets_secret="YOUTUBE_CLIENT_SECRETS_FILE",
    youtube_token_file_secret="YOUTUBE_TOKEN_FILE",
    automation_settings_path=Path("data/daily_automation_settings.json"),
    topics_used_path=Path("data/daily_topics_used.json"),
    run_history_path=Path("data/daily_run_history.json"),
    preset_overrides={},
)

CONSPIRACY_CHANNEL = ChannelProfile(
    channel_id="conspiracy",
    channel_name="Conspiracy Theory Channel",
    topic_direction=(
        "fringe conspiracy theory, government cover-up, secret society, "
        "paranormal, unexplained mystery, hidden truth, deep state"
    ),
    youtube_hashtags=["#shorts", "#conspiracy", "#conspiracytheory"],
    youtube_subscribe_cta="Subscribe for more conspiracy theories and hidden truths!",
    youtube_extra_tags=["conspiracy", "conspiracytheory", "shorts", "paranormal", "coverup"],
    youtube_category_id="24",  # Entertainment
    youtube_client_secrets_secret="YOUTUBE_CLIENT_SECRETS_FILE_CONSPIRACY",
    youtube_token_file_secret="YOUTUBE_TOKEN_FILE_CONSPIRACY",
    automation_settings_path=Path("data/conspiracy_automation_settings.json"),
    topics_used_path=Path("data/conspiracy_topics_used.json"),
    run_history_path=Path("data/conspiracy_run_history.json"),
    preset_overrides={
        "last_scene_cta_text": "Subscribe for more conspiracy theories and hidden truths!",
        "visual_style": "Cinematic dark",
        "openai_tts_voice": "onyx",
    },
)

_CHANNEL_REGISTRY: dict[str, ChannelProfile] = {
    "history": HISTORY_CHANNEL,
    "conspiracy": CONSPIRACY_CHANNEL,
}


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _get_openai_api_key() -> tuple[str, str]:
    for env_name in ("OPENAI_API_KEY", "openai_api_key"):
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value, f"env:{env_name}"

    try:
        config = get_openai_config()
        config_key = str(config.get("api_key") or "").strip()
        if config_key:
            return config_key, "config:get_openai_config"
    except Exception:
        pass

    return "", "missing"

def _default_daily_automation_settings() -> dict[str, Any]:
    return {
        "topic_override": "",
        "topic_direction": "",
        "selected_music_track": "",
        "preset": DAILY_SHORT_PRESET.as_dict(),
    }


def load_daily_automation_settings(path: Path = DAILY_AUTOMATION_SETTINGS_PATH) -> dict[str, Any]:
    defaults = _default_daily_automation_settings()
    if not path.exists():
        return defaults
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(payload, dict):
        return defaults
    preset_payload = payload.get("preset") if isinstance(payload.get("preset"), dict) else {}
    return {
        "topic_override": str(payload.get("topic_override", "") or "").strip(),
        "topic_direction": str(payload.get("topic_direction", "") or "").strip(),
        "selected_music_track": str(payload.get("selected_music_track", "") or "").strip(),
        "preset": {**DAILY_SHORT_PRESET.as_dict(), **preset_payload},
    }


def save_daily_automation_settings(settings: dict[str, Any], path: Path = DAILY_AUTOMATION_SETTINGS_PATH) -> None:
    payload = load_daily_automation_settings(path)
    payload.update(
        {
            "topic_override": str(settings.get("topic_override", payload.get("topic_override", "")) or "").strip(),
            "topic_direction": str(settings.get("topic_direction", payload.get("topic_direction", "")) or "").strip(),
            "selected_music_track": str(settings.get("selected_music_track", payload.get("selected_music_track", "")) or "").strip(),
        }
    )
    preset_updates = settings.get("preset") if isinstance(settings.get("preset"), dict) else {}
    payload["preset"] = {**DAILY_SHORT_PRESET.as_dict(), **payload.get("preset", {}), **preset_updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _coerce_int(value: Any, fallback: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(min_value, min(max_value, parsed))


def _coerce_float(value: Any, fallback: float, *, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(min_value, min(max_value, parsed))


def _resolve_daily_short_preset(settings: dict[str, Any], extra_overrides: dict | None = None) -> DailyShortPreset:
    preset_payload = settings.get("preset") if isinstance(settings.get("preset"), dict) else {}
    if extra_overrides:
        preset_payload = {**preset_payload, **extra_overrides}
    base = DAILY_SHORT_PRESET
    return replace(
        base,
        visual_style=str(preset_payload.get("visual_style", base.visual_style) or base.visual_style),
        effects_style=str(preset_payload.get("effects_style", base.effects_style) or base.effects_style),
        openai_tts_model=str(preset_payload.get("openai_tts_model", base.openai_tts_model) or base.openai_tts_model),
        openai_tts_voice=str(preset_payload.get("openai_tts_voice", base.openai_tts_voice) or base.openai_tts_voice),
        scene_count=_coerce_int(preset_payload.get("scene_count", base.scene_count), base.scene_count, min_value=1, max_value=75),
        subtitles_enabled=bool(preset_payload.get("subtitles_enabled", base.subtitles_enabled)),
        music_enabled=bool(preset_payload.get("music_enabled", base.music_enabled)),
        music_relative_level=_coerce_float(preset_payload.get("music_relative_level", base.music_relative_level), base.music_relative_level, min_value=0.0, max_value=1.0),
        target_word_count=_coerce_int(preset_payload.get("target_word_count", base.target_word_count), base.target_word_count, min_value=60, max_value=500),
        target_duration_seconds=_coerce_int(preset_payload.get("target_duration_seconds", base.target_duration_seconds), base.target_duration_seconds, min_value=30, max_value=180),
        last_scene_cta_text=str(preset_payload.get("last_scene_cta_text", base.last_scene_cta_text) or base.last_scene_cta_text),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _project_id_for_day(run_date: date, channel_id: str = "daily") -> str:
    ts = datetime.now(timezone.utc).strftime("%H%M")
    return f"{channel_id}_daily_{run_date.isoformat().replace('-', '_')}_{ts}"


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


def generate_daily_short_script(topic: str, preset: DailyShortPreset = DAILY_SHORT_PRESET, channel_name: str = "History Crossroads") -> str:
    api_key, api_key_source = _get_openai_api_key()

    model = "gpt-4o-mini"
    try:
        config = get_openai_config()
        model = str(config.get("model") or model).strip()
    except Exception:
        pass

    if not api_key:
        has_openai_env = bool((os.environ.get("openai_api_key") or "").strip())
        has_lower_alias = bool((os.environ.get("openai_api_key") or "").strip())
        raise RuntimeError(
            "Missing OpenAI API key for daily short script generation. "
            f"Resolution source={api_key_source}. "
            f"Env OPENAI_API_KEY present={has_openai_env}; openai_api_key present={has_lower_alias}. "
            "If running in GitHub Actions, confirm repository secret OPENAI_API_KEY exists and is mapped to job env.OPENAI_API_KEY. "
            "For local/Streamlit runs, set OPENAI_API_KEY (or openai_api_key) in .streamlit/secrets.toml."
        )

    from openai import OpenAI

    prompt = (
        f"Write a voiceover-only script for this topic: {topic}. "
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
            {"role": "system", "content": "You write engaging, accurate short-form voiceover scripts for YouTube Shorts."},
            {"role": "user", "content": prompt},
        ],
    )
    return str(resp.choices[0].message.content or "").strip()


def _upload_final_to_generated_bucket(project_id: str, final_path: Path, run_date: date) -> dict[str, str]:
    if not final_path.exists() or final_path.stat().st_size < 100000:
        raise RuntimeError(
            f"Final render too small ({final_path.stat().st_size} bytes) — render likely failed. "
            f"Path: {final_path}"
        )
    object_path = f"daily-renders/{run_date.isoformat()}/{project_id}_{final_path.name}"
    public_url = _sb_store.upload_video_bytes(
        bucket=SUPABASE_VIDEO_BUCKET,
        storage_path=object_path,
        video_bytes=final_path.read_bytes(),
    )
    if not public_url:
        raise RuntimeError(f"Failed to upload render to Supabase bucket '{SUPABASE_VIDEO_BUCKET}'.")
    return {"bucket": SUPABASE_VIDEO_BUCKET, "object_path": object_path, "public_url": public_url}


def run_daily_video_job(run_date: date | None = None, profile: ChannelProfile = HISTORY_CHANNEL) -> dict[str, Any]:
    target_date = run_date or date.today()
    project_id = _project_id_for_day(target_date, channel_id=profile.channel_id)
    timestamp = _utc_now().isoformat()

    settings = load_daily_automation_settings(path=profile.automation_settings_path)
    preset = _resolve_daily_short_preset(settings, extra_overrides=profile.preset_overrides or {})
    used_topics = load_used_topics(path=profile.topics_used_path)

    topic_override = str(settings.get("topic_override", "") or "").strip()
    # Profile topic_direction is the base; settings can further refine it
    topic_direction = str(settings.get("topic_direction", "") or "").strip() or profile.topic_direction
    topic = topic_override or generate_daily_topic(used_topics=used_topics, topic_direction=topic_direction)

    script_text = generate_daily_short_script(topic, preset, channel_name=profile.channel_name)
    music_track = str(settings.get("selected_music_track", "") or "").strip() or _resolve_default_music_track()
    if preset.music_enabled and not music_track:
        print(f"WARNING: music_enabled=True but no music track found in data/music_library — running without music.", file=sys.stderr)
        preset = replace(preset, music_enabled=False)

    ensure_project_files(project_id)
    upsert_project(project_id, f"{profile.channel_name} — {target_date.isoformat()}")

    payload = load_project_payload(project_id)
    payload.update(
        {
            "title": f"{profile.channel_name} — {target_date.isoformat()}",
            "channel_id": profile.channel_id,
            "channel_name": profile.channel_name,
            "topic": topic,
            "topic_direction": topic_direction,
            "script_text": script_text,
            "script_profile": "youtube_short_60s",
            "automation_mode": "existing_script_full_workflow",
            "aspect_ratio": preset.aspect_ratio,
            "output_width": preset.output_width,
            "output_height": preset.output_height,
            "scene_count": preset.scene_count,
            "max_scenes": preset.scene_count,
            "visual_style": preset.visual_style,
            "enable_video_effects": True,
            "video_effects_style": preset.effects_style,
            "enable_subtitles": preset.subtitles_enabled,
            "automation_include_captions": preset.subtitles_enabled,
            "burn_subtitles": preset.burn_subtitles,
            "generate_srt": preset.generate_srt,
            "enable_music": preset.music_enabled,
            "music_volume_relative_to_voiceover": preset.music_relative_level,
            "selected_music_track": music_track if preset.music_enabled else "",
            "tts_provider": preset.voice_provider,
            "openai_tts_model": preset.openai_tts_model,
            "openai_tts_voice": preset.openai_tts_voice,
            "daily_preset": preset.as_dict(),
            "daily_job_run_date": target_date.isoformat(),
            "daily_job_started_at": timestamp,
        }
    )
    save_project_payload(project_id, payload)
    (project_dir(project_id) / "script.txt").write_text(script_text, encoding="utf-8")

    # Checkpoint 1: verify script is substantial
    print(f"[Checkpoint 1] channel={profile.channel_id} topic={topic!r} script_len={len(script_text)}", file=sys.stderr)
    if len(script_text) < 50:
        raise RuntimeError(
            f"Generated script is too short ({len(script_text)} chars); expected at least 50 characters."
        )

    # Checkpoint 2: run full workflow
    pipeline = preset.to_pipeline_options(topic=topic, selected_music_track=music_track if preset.music_enabled else "")
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
    print(
        f"[Checkpoint 2] Workflow returned. failed_step={run_result.failed_step!r}  warnings={run_result.warnings}",
        file=sys.stderr,
    )

    # Checkpoint 3: abort on workflow failure
    if run_result.failed_step:
        raise RuntimeError(
            f"Workflow failed at step '{run_result.failed_step}': {'; '.join(run_result.warnings)}"
        )

    # Checkpoint 4: verify final render file exists and is large enough
    final_path = Path(run_result.final_output_path or project_dir(project_id) / "renders/final.mp4")
    if not final_path.exists():
        raise RuntimeError(
            f"Final render file not found at {final_path}. "
            "Image generation likely failed — verify that GEMINI_API_KEY is set correctly."
        )
    final_size = final_path.stat().st_size
    print(f"[Checkpoint 4] Final render file size: {final_size} bytes", file=sys.stderr)
    if final_size <= 100_000:
        raise RuntimeError(
            f"Final render is too small ({final_size} bytes; expected > 100,000). "
            "Image generation likely failed — verify that GEMINI_API_KEY is set correctly."
        )

    # Checkpoint 5: upload to Supabase
    upload_result = _upload_final_to_generated_bucket(project_id, final_path, target_date)
    save_used_topic(topic, run_date=target_date, path=profile.topics_used_path)
    print(f"[Checkpoint 5] Supabase upload complete. public_url={upload_result['public_url']}", file=sys.stderr)

    # Checkpoint 6: YouTube upload (non-fatal; skipped if credentials are absent)
    youtube_video_id = ""
    youtube_url = ""
    _yt_client_secrets = Path(get_secret(profile.youtube_client_secrets_secret, f"client_secrets_{profile.channel_id}.json")).expanduser()
    _yt_token = Path(get_secret(profile.youtube_token_file_secret, f"token_{profile.channel_id}.json")).expanduser()
    if _yt_client_secrets.exists() and _yt_token.exists():
        try:
            _yt_hashtags = " ".join(profile.youtube_hashtags)
            _yt_title = f"{topic} {_yt_hashtags}"
            _yt_description = f"{topic}\n\n{profile.youtube_subscribe_cta}"
            _yt_tags = [w.lower() for w in topic.split() if w.isalpha()] + profile.youtube_extra_tags
            _yt_result = _yt_upload_video(
                video_path=final_path,
                title=_yt_title,
                description=_yt_description,
                tags=_yt_tags,
                category_id=profile.youtube_category_id,
                privacy_status="private",
                client_secrets_file=_yt_client_secrets,
                token_file=_yt_token,
            )
            youtube_video_id = _yt_result.video_id
            youtube_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
            print(f"[Checkpoint 6] YouTube upload complete. video_id={youtube_video_id} url={youtube_url}", file=sys.stderr)
        except Exception as exc:
            print(f"[Checkpoint 6] YouTube upload failed (non-fatal): {exc}", file=sys.stderr)
    else:
        print(f"[Checkpoint 6] YouTube credentials not found for channel={profile.channel_id} — skipping.", file=sys.stderr)

    summary = {
        "timestamp": timestamp,
        "date": target_date.isoformat(),
        "channel_id": profile.channel_id,
        "channel_name": profile.channel_name,
        "project_id": project_id,
        "topic": topic,
        "status": "success",
        "final_render_path": str(final_path),
        "bucket": upload_result["bucket"],
        "bucket_path": upload_result["object_path"],
        "public_url": upload_result["public_url"],
        "subtitles_enabled": preset.subtitles_enabled,
        "tts_provider": preset.voice_provider,
        "openai_tts_model": preset.openai_tts_model,
        "openai_tts_voice": preset.openai_tts_voice,
        "music_track": music_track if preset.music_enabled else "",
        "music_relative_level": preset.music_relative_level,
        "scene_count": preset.scene_count,
        "youtube_video_id": youtube_video_id,
        "youtube_url": youtube_url,
    }
    _append_run_history(summary, path=profile.run_history_path)

    payload = load_project_payload(project_id)
    payload["daily_job_last_result"] = summary
    payload["daily_job_completed_at"] = _utc_now().isoformat()
    payload["generated_video_bucket_path"] = upload_result["object_path"]
    payload["generated_video_public_url"] = upload_result["public_url"]
    payload["enable_subtitles"] = preset.subtitles_enabled
    payload["youtube_video_id"] = youtube_video_id
    payload["youtube_url"] = youtube_url
    save_project_payload(project_id, payload)
    return summary


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run the History Forge daily video job.")
    parser.add_argument(
        "--channel",
        default="history",
        choices=list(_CHANNEL_REGISTRY.keys()),
        help="Which channel profile to run (default: history)",
    )
    args = parser.parse_args()
    profile = _CHANNEL_REGISTRY[args.channel]

    try:
        result = run_daily_video_job(profile=profile)
    except Exception as exc:
        import traceback
        print(f"DAILY_JOB_FAILED [{profile.channel_id}]: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    print(f"DAILY_JOB_SUCCESS [{profile.channel_id}]")
    print(f"project_id={result['project_id']}")
    print(f"topic={result['topic']}")
    print(f"final_render_path={result['final_render_path']}")
    print(f"bucket_path={result['bucket_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
