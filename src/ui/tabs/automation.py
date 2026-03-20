from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

import streamlit as st

from src.audio import (
    TTS_PROVIDER_ELEVENLABS,
    TTS_PROVIDER_OPENAI,
    get_openai_tts_models,
    get_openai_tts_voices,
    get_tts_provider_options,
    resolve_tts_settings,
)
from src.ai_video_generation import veo_configured, sora_configured
from src.video.ai_video_clips import SUPPORTED_PROVIDERS
from src.ui.constants import VISUAL_STYLE_OPTIONS
from src.ui.state import DEFAULT_VOICE_ID
from src.workflow.models import PIPELINE_STEPS
from src.workflow.state import reset_downstream_steps
from src.workflow.assets import (
    canonical_scene_image_path,
    preflight_report,
    rebuild_timeline_from_disk,
    regenerate_missing_scene_assets,
    resolve_music_track_for_project,
)
from src.workflow.models import StepStatus
from src.workflow.project_io import load_project_payload, load_scenes, project_dir, save_project_payload, save_scenes
from src.video.render_settings import normalize_video_effects_style, render_resolution_for_aspect_ratio
from src.workflow.services import (
    FullWorkflowOptions,
    PipelineOptions,
    run_full_workflow,
    run_render_video,
    run_sync_timeline,
)
from src.workflow.state import load_workflow_state, save_workflow_state
from src.workflow.daily_job import load_daily_automation_settings, save_daily_automation_settings

MUSIC_LIBRARY_ROOT = Path("data/music_library")
PREFERENCES_PATH = Path("data/user_preferences.json")
AUTOMATION_STEP_ORDER_TOPIC: tuple[str, ...] = ("script", "voiceover", "scenes", "narrative", "prompts", "images", "effects", "ai_video_clips", "render")
AUTOMATION_STEP_ORDER_SCRIPT: tuple[str, ...] = ("voiceover", "scenes", "narrative", "prompts", "images", "effects", "ai_video_clips", "render")


def _tail_file(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return "".join(deque(handle, maxlen=lines))


def _tail_workflow_log(project_id: str, n: int = 50) -> list[str]:
    log_path = project_dir(project_id) / "workflow.log"
    tailed = _tail_file(log_path, lines=n)
    return [line for line in tailed.splitlines() if line.strip()]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_saved_voice_preference() -> str:
    if not PREFERENCES_PATH.exists():
        return ""
    try:
        payload = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("voice_id", "") or "").strip()


def _resolve_voice_id(selected_voice_id: str, payload: dict[str, Any]) -> tuple[str, str]:
    selected = str(selected_voice_id or "").strip()
    if selected:
        return selected, "selected"
    project_voice = str(payload.get("voice_id", "") or "").strip()
    if project_voice:
        return project_voice, "project"
    saved = _load_saved_voice_preference()
    if saved:
        return saved, "saved_preference"
    fallback = str(DEFAULT_VOICE_ID or "").strip()
    if fallback:
        return fallback, "DEFAULT_VOICE_ID"
    return "", "unresolved"


def _count_files(folder: Path, suffixes: tuple[str, ...]) -> int:
    if not folder.exists():
        return 0
    valid = {s.lower() for s in suffixes}
    return sum(1 for path in folder.glob("*") if path.is_file() and path.suffix.lower() in valid)


def _list_music_tracks(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted([p for p in folder.glob("*.*") if p.suffix.lower() in {".mp3", ".wav", ".m4a"}], key=lambda p: p.name.lower())


def _reset_step(project_id: str, step_name: str) -> None:
    state = load_workflow_state(project_id)
    state.step_statuses[step_name] = StepStatus.NOT_STARTED
    state.timestamps.pop(step_name, None)
    state.retry_counts[step_name] = 0
    if state.current_stage == step_name:
        state.current_stage = step_name
    state.last_error = ""
    save_workflow_state(project_id, state)


def _asset_counts(project_id: str) -> dict[str, int]:
    project_path = project_dir(project_id)
    scenes = load_scenes(project_id)
    prompt_count = sum(1 for scene in scenes if (getattr(scene, "image_prompt", "") or "").strip())
    ai_video_count = sum(1 for scene in scenes if (getattr(scene, "video_path", "") or getattr(scene, "video_url", "") or "").strip())
    return {
        "scenes": len(scenes),
        "prompts": prompt_count,
        "images": _count_files(project_path / "assets/images", (".png", ".jpg", ".jpeg", ".webp")),
        "voiceover": _count_files(project_path / "assets/audio", (".mp3", ".wav", ".m4a")),
        "music": _count_files(project_path / "assets/music", (".mp3", ".wav", ".m4a")),
        "videos": _count_files(project_path / "assets/videos", (".mp4", ".mov", ".webm", ".mkv")),
        "ai_video_scenes": ai_video_count,
    }


def _automation_mode(project_payload: dict[str, Any]) -> str:
    mode = str(project_payload.get("automation_mode", "topic_to_short_video") or "topic_to_short_video").strip()
    return mode if mode in {"topic_to_short_video", "existing_script_full_workflow"} else "topic_to_short_video"


def _automation_steps_for_mode(mode: str) -> tuple[str, ...]:
    return AUTOMATION_STEP_ORDER_TOPIC if mode == "topic_to_short_video" else AUTOMATION_STEP_ORDER_SCRIPT


def _coerce_float(raw_value: Any, fallback: float) -> float:
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return fallback


def _render_workflow_progress(project_id: str, mode: str, progress_holder: Any, log_holder: Any, error_holder: Any, output_holder: Any) -> None:
    state = load_workflow_state(project_id)
    step_order = _automation_steps_for_mode(mode)
    completed = sum(1 for step in step_order if state.step_statuses.get(step) in {StepStatus.COMPLETED, StepStatus.SKIPPED})
    ratio = completed / max(1, len(step_order))
    current = state.current_stage if state.current_stage in step_order else step_order[0]
    current_idx = step_order.index(current) + 1

    with progress_holder.container():
        st.progress(ratio)
        step_label = current.replace('_', ' ').title()
        if current == "ai_video_clips":
            clips_progress = st.session_state.get("ai_clips_progress", "")
            detail = f" — {clips_progress}" if clips_progress else " (this takes 5-10 minutes, please keep this tab open)"
            st.write(f"Running step {current_idx} of {len(step_order)}: {step_label}{detail}")
        else:
            st.write(f"Running step {current_idx} of {len(step_order)}: {step_label}")
        cols = st.columns(len(step_order))
        for idx, step in enumerate(step_order):
            status = state.step_statuses.get(step, StepStatus.NOT_STARTED)
            cols[idx].markdown(f"**{step.title()}**\n\n`{status.value}`")

    recent_lines = _tail_workflow_log(project_id, n=60)
    with log_holder.container():
        st.caption("Recent workflow log")
        st.code("\n".join(recent_lines) if recent_lines else "No workflow.log lines yet.", language="text")

    if state.last_error:
        error_holder.error(f"Last error: {state.last_error}")
    else:
        error_holder.empty()

    final_render = project_dir(project_id) / "renders" / "final.mp4"
    if final_render.exists():
        output_holder.success(f"Final render path: {final_render}")


def _render_post_run_video_section(project_id: str, final_render: Path) -> None:
    st.markdown("#### Final Video")
    if not final_render.exists():
        st.info("Run **Render Final Video** (or full workflow) to preview and download the final MP4 here.")
        return

    st.video(str(final_render))
    st.write(f"Final render path: `{final_render}`")
    try:
        video_bytes = final_render.read_bytes()
    except OSError as exc:
        st.warning(f"Final render exists but could not be read for download: {exc}")
        return
    st.download_button(
        "⬇️ Download Final Video",
        data=video_bytes,
        file_name=f"{project_id}_final.mp4",
        mime="video/mp4",
        width="stretch",
    )


def _render_quick_scene_edits(project_id: str, scenes: list[Any]) -> None:
    st.markdown("#### Continue Editing (Scenes / Images)")
    if not scenes:
        st.info("No scenes available yet. Generate scenes first, then edit here or in the **Scenes** and **Images** tabs.")
        return

    scene_labels = [f"Scene {scene.index}: {str(getattr(scene, 'title', '') or 'Untitled')}" for scene in scenes]
    selected_label = st.selectbox("Scene to edit", options=scene_labels, key=f"automation_scene_editor_{project_id}")
    selected_idx = scene_labels.index(selected_label)
    selected = scenes[selected_idx]

    edited_title = st.text_input("Title", value=str(getattr(selected, "title", "") or ""), key=f"automation_scene_title_{project_id}_{selected.index}")
    edited_excerpt = st.text_area(
        "Script excerpt",
        value=str(getattr(selected, "script_excerpt", "") or ""),
        key=f"automation_scene_excerpt_{project_id}_{selected.index}",
        height=130,
    )
    edited_prompt = st.text_area(
        "Image prompt",
        value=str(getattr(selected, "image_prompt", "") or ""),
        key=f"automation_scene_prompt_{project_id}_{selected.index}",
        height=130,
    )

    image_path = canonical_scene_image_path(project_id, int(selected.index))
    if image_path.exists():
        st.image(str(image_path), caption=f"Current image · {image_path.name}", width="stretch")
    else:
        st.caption("No image file found for this scene yet.")

    if st.button("Save scene edits", key=f"automation_save_scene_{project_id}_{selected.index}", width="stretch"):
        selected.title = edited_title.strip()
        selected.script_excerpt = edited_excerpt.strip()
        selected.image_prompt = edited_prompt.strip()
        save_scenes(project_id, scenes)
        st.success("Scene updates saved. Re-run prompts/images/render to apply changes to the final video.")




def _load_daily_run_history() -> list[dict[str, Any]]:
    path = Path("data/daily_run_history.json")
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _render_daily_automation_status(project_id: str) -> None:
    st.markdown("#### Daily Automation")
    history = _load_daily_run_history()
    last = history[-1] if history else {}
    settings = load_daily_automation_settings()
    preset = settings.get("preset", {}) if isinstance(settings.get("preset"), dict) else {}

    st.caption("Schedule is controlled by `.github/workflows/daily-video.yml` and is editable from this page.")

    cron_default = "0 7 * * *"
    workflow_path = Path(".github/workflows/daily-video.yml")
    workflow_text = workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else ""
    cron_match = None
    import re

    cron_match = re.search(r"cron:\s*'([^']+)'", workflow_text)
    current_cron = cron_match.group(1) if cron_match else cron_default

    col1, col2 = st.columns(2)
    with col1:
        st.text(f"Last daily run: {last.get('timestamp', 'n/a')}")
        st.text(f"Last topic: {last.get('topic', 'n/a')}")
        st.text(f"Last project ID: {last.get('project_id', 'n/a')}")
    with col2:
        st.text(f"Last render path: {last.get('final_render_path', 'n/a')}")
        upload_value = last.get('bucket_path') or last.get('public_url') or 'n/a'
        st.text(f"Last bucket upload: {upload_value}")
        st.text(f"Last subtitles enabled: {last.get('subtitles_enabled', 'n/a')}")

    st.markdown("##### Daily Job Settings")
    schedule_cron = st.text_input("Daily schedule cron (UTC)", value=current_cron, help="Example: 0 7 * * * for 07:00 UTC daily")
    topic_override = st.text_input("Topic override (optional)", value=str(settings.get("topic_override", "") or ""), help="When set, the daily job uses this topic instead of random selection.")
    topic_direction = st.text_area(
        "Topic direction (optional)",
        value=str(settings.get("topic_direction", "") or ""),
        help="Guide daily topic selection with a reusable direction, e.g. historical mysteries or unsung heroes.",
        height=110,
    )
    scene_count = st.number_input("Daily scene count", min_value=1, max_value=75, value=int(preset.get("scene_count", 14) or 14), step=1)
    target_word_count = st.number_input("Script target words", min_value=60, max_value=500, value=int(preset.get("target_word_count", 150) or 150), step=5)
    target_duration = st.number_input("Target duration (seconds)", min_value=30, max_value=180, value=int(preset.get("target_duration_seconds", 60) or 60), step=5)
    subtitles_enabled = st.toggle("Enable subtitles in daily automation", value=bool(preset.get("subtitles_enabled", False)))
    music_enabled = st.toggle("Enable music in daily automation", value=bool(preset.get("music_enabled", True)))
    music_level = st.slider("Daily music level", min_value=0.0, max_value=1.0, step=0.05, value=float(preset.get("music_relative_level", 0.15) or 0.15), disabled=not music_enabled)

    _daily_effect_options = ["Ken Burns - Standard", "Ken Burns - Strong", "Ken Burns - Dramatic", "Off"]
    _daily_effect_default = str(preset.get("effects_style", "Ken Burns - Standard") or "Ken Burns - Standard")
    if _daily_effect_default not in _daily_effect_options:
        _daily_effect_default = "Ken Burns - Standard"
    daily_effects_style = st.selectbox("Daily video effects style", options=_daily_effect_options, index=_daily_effect_options.index(_daily_effect_default))

    _daily_transition_options = {"Random": "random", "Fade": "fade", "Fade to Black": "fadeblack", "Wipe Left": "wipeleft", "Wipe Right": "wiperight", "Slide Left": "slideleft", "Slide Right": "slideright"}
    _daily_transition_value = str(preset.get("scene_transition_type", "fade") or "fade")
    _daily_transition_label = {v: k for k, v in _daily_transition_options.items()}.get(_daily_transition_value, "Fade")
    daily_transition_label = st.selectbox("Daily scene transition", options=list(_daily_transition_options.keys()), index=list(_daily_transition_options.keys()).index(_daily_transition_label))
    daily_transition_type = _daily_transition_options[daily_transition_label]

    _daily_ai_options = ["None", "sora", "veo"]
    _daily_ai_default = str(preset.get("ai_video_provider", "sora") or "sora")
    if _daily_ai_default not in _daily_ai_options:
        _daily_ai_default = "sora"
    daily_ai_provider = st.selectbox("Daily AI video provider", options=_daily_ai_options, index=_daily_ai_options.index(_daily_ai_default), format_func=lambda p: {"None": "None", "sora": "OpenAI Sora", "veo": "Google Veo"}.get(p, p))

    project_music_tracks = _list_music_tracks(project_dir(project_id) / "assets/music")
    shared_music_tracks = _list_music_tracks(MUSIC_LIBRARY_ROOT)
    combined_tracks = [str(p) for p in project_music_tracks + shared_music_tracks]
    saved_track = str(settings.get("selected_music_track", "") or "")
    if music_enabled and combined_tracks:
        selected_idx = combined_tracks.index(saved_track) if saved_track in combined_tracks else 0
        selected_daily_music = st.selectbox("Daily music track", options=combined_tracks, index=selected_idx)
    else:
        selected_daily_music = ""

    if st.button("Save daily automation settings", width="stretch"):
        save_daily_automation_settings({
            "topic_override": topic_override.strip(),
            "topic_direction": topic_direction.strip(),
            "selected_music_track": selected_daily_music if music_enabled else "",
            "preset": {
                "scene_count": int(scene_count),
                "target_word_count": int(target_word_count),
                "target_duration_seconds": int(target_duration),
                "subtitles_enabled": bool(subtitles_enabled),
                "music_enabled": bool(music_enabled),
                "music_relative_level": float(music_level),
                "effects_style": daily_effects_style,
                "scene_transition_type": daily_transition_type,
                "ai_video_provider": daily_ai_provider if daily_ai_provider != "None" else "",
            },
        })
        if workflow_path.exists() and schedule_cron.strip() and schedule_cron.strip() != current_cron:
            updated = re.sub(r"cron:\s*'[^']+'", f"cron: '{schedule_cron.strip()}'", workflow_text, count=1)
            workflow_path.write_text(updated, encoding="utf-8")
        st.success("Daily automation settings saved.")

def tab_automation(project_id: str) -> None:
    st.subheader("Automation")
    _render_daily_automation_status(project_id)
    project_path = project_dir(project_id)
    payload = load_project_payload(project_id)
    state = load_workflow_state(project_id)
    counts = _asset_counts(project_id)

    _daily_settings = load_daily_automation_settings()
    _daily_preset = _daily_settings.get("preset", {}) if isinstance(_daily_settings.get("preset"), dict) else {}

    automation_mode = _automation_mode(payload)
    script_text = str(payload.get("script_text", "") or "").strip()
    final_render = project_path / "renders" / "final.mp4"
    timeline_path = project_path / "timeline.json"
    render_report = project_path / "renders" / "render_report.json"
    workflow_log = project_path / "workflow.log"
    scenes = load_scenes(project_id)

    progress_holder = st.empty()
    error_holder = st.empty()
    log_holder = st.empty()
    output_holder = st.empty()
    _render_workflow_progress(project_id, automation_mode, progress_holder, log_holder, error_holder, output_holder)

    st.markdown("#### Automation Settings")
    mode_label_map = {
        "Topic → 60s Short Video": "topic_to_short_video",
        "Existing Script → Full Workflow": "existing_script_full_workflow",
    }
    current_mode_label = "Topic → 60s Short Video" if automation_mode == "topic_to_short_video" else "Existing Script → Full Workflow"
    selected_mode_label = st.selectbox("Automation Mode", options=list(mode_label_map.keys()), index=list(mode_label_map.keys()).index(current_mode_label))
    selected_mode = mode_label_map[selected_mode_label]

    topic_input = str(payload.get("topic", "") or "")
    topic_direction = str(payload.get("topic_direction", payload.get("story_angle", "")) or "")
    if selected_mode == "topic_to_short_video":
        topic_input = st.text_input("Topic (required)", value=topic_input)
        topic_direction = st.text_input("Angle / Direction (optional)", value=topic_direction)
    elif not script_text:
        st.warning("Existing Script mode selected, but no script currently exists in this project.")

    st.caption(f"Current mode: `{selected_mode}`")
    st.caption(f"Script status: {'available' if script_text else 'missing'}")
    project_music_tracks = _list_music_tracks(project_path / "assets/music")
    shared_music_tracks = _list_music_tracks(MUSIC_LIBRARY_ROOT)

    saved_voice_id = _load_saved_voice_preference()
    known_voice_ids = [DEFAULT_VOICE_ID]
    for candidate in [str(payload.get("voice_id", "") or ""), saved_voice_id]:
        if candidate and candidate not in known_voice_ids:
            known_voice_ids.append(candidate)

    current_style = str(payload.get("visual_style", payload.get("image_style", VISUAL_STYLE_OPTIONS[0])) or VISUAL_STYLE_OPTIONS[0])
    if current_style not in VISUAL_STYLE_OPTIONS:
        current_style = VISUAL_STYLE_OPTIONS[0]

    col_settings_1, col_settings_2 = st.columns(2)
    default_ratio = str(payload.get("aspect_ratio", "") or "").strip()
    if default_ratio not in {"16:9", "9:16"}:
        default_ratio = "9:16" if selected_mode == "topic_to_short_video" else "16:9"

    raw_scene_count = payload.get("scene_count", payload.get("max_scenes", 8))
    try:
        default_scene_count = int(raw_scene_count or 8)
    except (TypeError, ValueError):
        default_scene_count = 8
    default_scene_count = max(1, min(75, default_scene_count))

    payload_enable_effects = payload.get("enable_video_effects")
    default_enable_effects = bool(payload_enable_effects) if payload_enable_effects is not None else True
    default_effect_style = normalize_video_effects_style(
        payload.get("video_effects_style") or _daily_preset.get("effects_style", "Ken Burns - Standard"),
        enable_motion=default_enable_effects,
    )
    payload_enable_subtitles = payload.get("enable_subtitles", payload.get("automation_include_captions"))
    default_enable_subtitles = bool(payload_enable_subtitles) if payload_enable_subtitles is not None else (selected_mode == "topic_to_short_video")
    payload_enable_music = payload.get("enable_music", payload.get("include_music"))
    default_enable_music = bool(payload_enable_music) if payload_enable_music is not None else False
    _music_vol_fallback = float(_daily_preset.get("music_relative_level", 0.15) or 0.15)
    default_music_volume = min(1.0, max(0.0, _coerce_float(payload.get("music_volume_relative_to_voiceover") or _music_vol_fallback, _music_vol_fallback)))

    with col_settings_1:
        aspect_ratio = st.selectbox("Aspect Ratio", options=["16:9", "9:16"], index=0 if default_ratio == "16:9" else 1)
        visual_style = st.selectbox("Visual Style", options=list(VISUAL_STYLE_OPTIONS), index=list(VISUAL_STYLE_OPTIONS).index(current_style))
        scene_count = st.number_input("Number of Scenes", min_value=1, max_value=75, value=default_scene_count, step=1)
        enable_video_effects = st.toggle("Video Effects", value=default_enable_effects)
        video_effects_style = st.selectbox("Video Effects Style", options=["Off", "Ken Burns - Standard", "Ken Burns - Strong", "Ken Burns - Dramatic"], index=["Off", "Ken Burns - Standard", "Ken Burns - Strong", "Ken Burns - Dramatic"].index(default_effect_style), disabled=not enable_video_effects)

    # ── AI Video Clips ─────────────────────────────────────────────
    st.subheader("🎬 AI Video Clips")
    _ai_video_provider_options = ["None", "Google Veo (Supabase)", "OpenAI Sora"]
    _ai_provider_internal_to_label = {"sora": "OpenAI Sora", "veo": "Google Veo (Supabase)"}
    _daily_ai_label = _ai_provider_internal_to_label.get(str(_daily_preset.get("ai_video_provider", "") or ""), "None")
    _default_ai_video_provider = str(payload.get("ai_video_provider") or _daily_ai_label or "None")
    if _default_ai_video_provider not in _ai_video_provider_options:
        _default_ai_video_provider = "None"
    ai_video_provider = st.selectbox(
        "AI Video Clip Generator",
        options=_ai_video_provider_options,
        index=_ai_video_provider_options.index(_default_ai_video_provider),
        key="auto_ai_video_provider",
        help="Generates 2 short AI video clips: one at the start, one at the midpoint of the final video.",
    )

    with st.expander("⚙️ AI Video Settings", expanded=False):
        _veo_ok = veo_configured()
        _sora_ok = sora_configured()
        _available = [p for p in SUPPORTED_PROVIDERS
                      if (p == "veo" and _veo_ok) or (p == "sora" and _sora_ok)]

        if not _available:
            st.warning(
                "No AI video providers are configured. "
                "Set SUPABASE credentials for Veo or openai_api_key for Sora."
            )
            _run_provider = "veo"
        else:
            _daily_run_provider = str(_daily_preset.get("ai_video_provider", "") or "")
            _global_default = (
                st.session_state.get("ai_video_provider")
                or (_daily_run_provider if _daily_run_provider in _available else None)
                or _available[0]
            )
            if _global_default not in _available:
                _global_default = _available[0]

            _run_provider = st.selectbox(
                "Provider for this run",
                _available,
                index=_available.index(_global_default),
                format_func=lambda p: f"{'🎬 Veo' if p == 'veo' else '🤖 Sora'}",
                help="Overrides the sidebar default for this automation run only.",
                key="automation_provider_override",
            )

        _aspect = st.selectbox(
            "Clip aspect ratio",
            ["9:16", "16:9", "1:1"],
            index=0,
            help="9:16 for Shorts/Reels, 16:9 for YouTube landscape.",
            key="automation_clip_aspect_ratio",
        )
        _duration = st.slider(
            "Clip duration (seconds)",
            min_value=4,
            max_value=12,
            value=5,
            step=1,
            help="Sora snaps to 4, 8, or 12. Veo uses the value as-is.",
            key="automation_clip_duration",
        )

        if _run_provider == "sora":
            st.info(
                "Sora will attempt image-to-video using generated scene images. "
                "If no image is available or the request fails, it falls back to text-to-video."
            )
        else:
            st.info(
                "Veo requires generated scene images. "
                "Run the Images step before AI Video Clips."
            )

    # Store resolved provider for use in the step runner.
    # If the main selectbox is "None", mark provider as empty so the step is skipped.
    _label_to_internal = {"Google Veo (Supabase)": "veo", "OpenAI Sora": "sora"}
    _main_selection_internal = _label_to_internal.get(ai_video_provider, "")
    if _main_selection_internal:
        st.session_state["automation_run_provider"] = _run_provider
    else:
        st.session_state["automation_run_provider"] = ""

    TRANSITION_LABEL_MAP: dict[str, str] = {
        "Random": "random",
        "Fade": "fade",
        "Fade to Black": "fadeblack",
        "Fade to White": "fadewhite",
        "Wipe Left": "wipeleft",
        "Wipe Right": "wiperight",
        "Slide Left": "slideleft",
        "Slide Right": "slideright",
        "Smooth Left": "smoothleft",
        "Smooth Right": "smoothright",
        "Circle Open": "circleopen",
        "Circle Close": "circleclose",
        "Distance": "distance",
    }
    TRANSITION_VALUE_TO_LABEL = {v: k for k, v in TRANSITION_LABEL_MAP.items()}
    default_transition_type = str(payload.get("scene_transition_type") or _daily_preset.get("scene_transition_type", "fade") or "fade").strip().lower()
    if default_transition_type not in TRANSITION_LABEL_MAP.values():
        default_transition_type = "fade"
    default_transition_label = TRANSITION_VALUE_TO_LABEL.get(default_transition_type, "Fade")

    with col_settings_2:
        enable_subtitles = st.toggle("Subtitles", value=default_enable_subtitles)
        enable_music = st.toggle("Background Music", value=default_enable_music)
        generate_voiceover = st.toggle("Generate voiceover", value=bool(payload.get("automation_generate_voiceover", True)))
        overwrite_existing = st.toggle("Overwrite existing assets", value=bool(payload.get("automation_overwrite_existing", False)))
        selected_transition_label = st.selectbox(
            "Scene Transition",
            options=list(TRANSITION_LABEL_MAP.keys()),
            index=list(TRANSITION_LABEL_MAP.keys()).index(default_transition_label),
            help="Transition effect between scenes. 'Random' picks a different transition for each scene.",
        )
        selected_transition_type = TRANSITION_LABEL_MAP[selected_transition_label]

    combined_music_choices: list[tuple[str, str]] = []
    for track in project_music_tracks:
        combined_music_choices.append((f"Project · {track.name}", str(track)))
    for track in shared_music_tracks:
        combined_music_choices.append((f"Shared · {track.name}", str(track)))

    selected_music_track = str(payload.get("selected_music_track") or _daily_settings.get("selected_music_track", "") or "")
    music_volume_relative_to_voiceover = st.slider(
        "Background Music Level (relative to voiceover)",
        min_value=0.0,
        max_value=1.0,
        step=0.05,
        value=default_music_volume,
        disabled=not enable_music,
        help="1.0 = same level as voiceover, 0.5 = quieter, 0.0 = muted.",
    )
    if enable_music:
        if combined_music_choices:
            labels = [label for label, _ in combined_music_choices]
            values = [value for _, value in combined_music_choices]
            selected_index = values.index(selected_music_track) if selected_music_track in values else 0
            selected_label = st.selectbox("Background Music Selection", options=labels, index=selected_index)
            selected_music_track = values[labels.index(selected_label)]
        else:
            st.warning("Background music is enabled, but no project/shared music tracks were found.")
            selected_music_track = ""

    tts_settings = resolve_tts_settings(payload)
    provider_options = get_tts_provider_options()
    selected_provider = st.selectbox(
        "Voice Provider",
        options=provider_options,
        index=provider_options.index(tts_settings.provider),
        format_func=lambda p: "ElevenLabs" if p == TTS_PROVIDER_ELEVENLABS else "OpenAI",
        key="automation_voice_provider",
    )

    selected_voice_id = str(payload.get("elevenlabs_voice_id", payload.get("voice_id", "")) or "")
    resolved_voice_id, resolved_source = _resolve_voice_id(selected_voice_id, payload)

    selected_openai_model = str(payload.get("openai_tts_model", tts_settings.openai_tts_model) or tts_settings.openai_tts_model)
    selected_openai_voice = str(payload.get("openai_tts_voice", tts_settings.openai_tts_voice) or tts_settings.openai_tts_voice)
    selected_openai_instructions = str(payload.get("openai_tts_instructions", tts_settings.openai_tts_instructions) or tts_settings.openai_tts_instructions)

    if selected_provider == TTS_PROVIDER_ELEVENLABS:
        selected_voice_id = st.selectbox(
            "ElevenLabs Voice ID",
            options=known_voice_ids,
            index=known_voice_ids.index(selected_voice_id) if selected_voice_id in known_voice_ids else 0,
            help="Automation resolves Voice ID in order: selected value, saved preference, then DEFAULT_VOICE_ID.",
            key="automation_elevenlabs_voice_id",
        )
        resolved_voice_id, resolved_source = _resolve_voice_id(selected_voice_id, payload)
        st.caption(f"Resolved Voice ID: `{resolved_voice_id or 'None'}` ({resolved_source})")
    else:
        model_options = get_openai_tts_models()
        voice_options = get_openai_tts_voices()
        if selected_openai_model not in model_options:
            selected_openai_model = "gpt-4o-mini-tts"
        if selected_openai_voice not in voice_options:
            selected_openai_voice = "alloy"
        selected_openai_model = st.selectbox("OpenAI TTS Model", options=model_options, index=model_options.index(selected_openai_model), key="automation_openai_tts_model")
        selected_openai_voice = st.selectbox("OpenAI Voice", options=voice_options, index=voice_options.index(selected_openai_voice), key="automation_openai_tts_voice")
        selected_openai_instructions = st.text_area(
            "Speaking style instructions (optional)",
            value=selected_openai_instructions,
            help="Style/tone guidance, especially for gpt-4o-mini-tts.",
            key="automation_openai_tts_instructions",
        )

    resolved_output_size = render_resolution_for_aspect_ratio(aspect_ratio)
    resolved_effect_style = normalize_video_effects_style(video_effects_style, enable_motion=enable_video_effects)
    st.caption(f"Pre-run summary · aspect_ratio={aspect_ratio} output_size={resolved_output_size} subtitles={enable_subtitles} effects={resolved_effect_style} transition={selected_transition_type} music_enabled={enable_music} music_track={selected_music_track or 'none'}")

    def _persist_current_settings() -> None:
        """Save the current widget values to the project payload."""
        safe_scene_count = max(1, min(75, int(scene_count)))
        safe_music_volume = min(1.0, max(0.0, float(music_volume_relative_to_voiceover)))
        payload.update({
            "aspect_ratio": aspect_ratio,
            "image_style": visual_style,
            "visual_style": visual_style,
            "scene_count": safe_scene_count,
            "max_scenes": safe_scene_count,
            "enable_video_effects": bool(enable_video_effects),
            "video_effects_style": normalize_video_effects_style(video_effects_style, enable_motion=enable_video_effects),
            "enable_music": bool(enable_music),
            "include_music": bool(enable_music),
            "selected_music_track": selected_music_track if enable_music else "",
            "enable_subtitles": bool(enable_subtitles),
            "automation_generate_voiceover": bool(generate_voiceover),
            "automation_overwrite_existing": bool(overwrite_existing),
            "music_volume_relative_to_voiceover": safe_music_volume,
            "scene_transition_type": selected_transition_type,
            "ai_video_provider": ai_video_provider,
            "tts_provider": selected_provider,
            "voice_id": selected_voice_id,
            "elevenlabs_voice_id": selected_voice_id,
            "openai_tts_model": selected_openai_model,
            "openai_tts_voice": selected_openai_voice,
            "openai_tts_instructions": selected_openai_instructions,
            "automation_mode": selected_mode,
            "topic": topic_input.strip(),
            "topic_direction": topic_direction.strip(),
            "script_profile": "youtube_short_60s" if selected_mode == "topic_to_short_video" else str(payload.get("script_profile", "") or ""),
        })
        save_project_payload(project_id, payload)

    if st.button("Save automation settings", width="stretch"):
        _persist_current_settings()
        st.success("Automation settings saved.")

    if generate_voiceover and selected_provider == TTS_PROVIDER_ELEVENLABS and not resolved_voice_id:
        st.warning("No Voice ID could be resolved. Select a voice before running automation.")
    if generate_voiceover and selected_provider == TTS_PROVIDER_OPENAI and not selected_openai_voice:
        st.warning("OpenAI voice is required before running automation.")
    if enable_music and not selected_music_track:
        st.warning("Background music is enabled, but no music track is selected.")

    pipeline_options = PipelineOptions(
        include_music=enable_music,
        include_subtitles=enable_subtitles,
        include_voiceover=bool(generate_voiceover),
        number_of_scenes=max(1, min(75, int(scene_count))),
        aspect_ratio=aspect_ratio,
        visual_style=visual_style,
        enable_video_effects=enable_video_effects,
        video_effects_style=normalize_video_effects_style(video_effects_style, enable_motion=enable_video_effects),
        selected_music_track=selected_music_track,
        music_volume_relative_to_voiceover=min(1.0, max(0.0, float(music_volume_relative_to_voiceover))),
        scene_transition_type=selected_transition_type,
        voice_id=selected_voice_id,
        tts_provider=selected_provider,
        elevenlabs_voice_id=selected_voice_id,
        openai_tts_model=selected_openai_model,
        openai_tts_voice=selected_openai_voice,
        openai_tts_instructions=selected_openai_instructions,
        allow_silent_render=False,
        automation_mode=selected_mode,
        topic=topic_input.strip(),
        topic_direction=topic_direction.strip(),
        script_profile="youtube_short_60s" if selected_mode == "topic_to_short_video" else str(payload.get("script_profile", "") or ""),
        ai_video_provider=st.session_state.get("automation_run_provider", "veo"),
    )

    st.markdown("#### Controls")
    c_full, c_resume, c_timeline, c_render = st.columns(4)
    c_assets, c_rebuild = st.columns(2)

    def _progress_callback(event: dict[str, Any]) -> None:
        _render_workflow_progress(project_id, selected_mode, progress_holder, log_holder, error_holder, output_holder)

    if c_full.button("Run Full Workflow", width="stretch"):
        if selected_mode == "topic_to_short_video" and not topic_input.strip():
            st.error("Topic is required for Topic → 60s Short Video mode.")
            return
        if selected_mode == "existing_script_full_workflow" and not script_text:
            st.error("Existing Script mode requires project script text.")
            return
        if enable_music and not selected_music_track:
            st.error("Background music is enabled but no track is selected.")
            return
        st.info(f"Settings Summary · Mode={selected_mode} | Topic={topic_input.strip() or 'n/a'} | Aspect={aspect_ratio} ({resolved_output_size}) | Style={visual_style} | Scenes={int(scene_count)} | Voice={selected_provider} | Subtitles={enable_subtitles} | Effects={normalize_video_effects_style(video_effects_style, enable_motion=enable_video_effects)} | Music={enable_music} | Track={selected_music_track or 'none'}")
        if generate_voiceover and selected_provider == TTS_PROVIDER_ELEVENLABS and not resolved_voice_id:
            st.error("Voice ID is required for ElevenLabs voiceover.")
            return
        _persist_current_settings()
        st.session_state.pop("ai_clips_progress", None)
        result = run_full_workflow(
            project_id,
            FullWorkflowOptions(
                mode="full_auto",
                overwrite_scenes=overwrite_existing,
                overwrite_prompts=overwrite_existing,
                overwrite_images=overwrite_existing,
                overwrite_voiceover=overwrite_existing and generate_voiceover,
                overwrite_timeline=overwrite_existing,
                overwrite_render=overwrite_existing,
                pipeline=pipeline_options,
                progress_callback=_progress_callback,
            ),
        )
        _render_workflow_progress(project_id, selected_mode, progress_holder, log_holder, error_holder, output_holder)
        if result.failed_step:
            st.error(f"Workflow stopped at: {result.failed_step}")
        else:
            st.success("Workflow run completed.")
        if result.final_output_path:
            st.success(f"Final render: {result.final_output_path}")
            st.rerun()

    if c_resume.button("Resume Missing Steps", width="stretch"):
        _persist_current_settings()
        result = run_full_workflow(project_id, FullWorkflowOptions(mode="resume_missing", pipeline=pipeline_options, progress_callback=_progress_callback))
        _render_workflow_progress(project_id, selected_mode, progress_holder, log_holder, error_holder, output_holder)
        if result.failed_step:
            st.error(f"Resume failed at: {result.failed_step}")
        else:
            st.success("Resume completed.")

    if c_timeline.button("Rebuild Timeline", width="stretch"):
        _persist_current_settings()
        result = run_sync_timeline(project_id, pipeline_options)
        if result.status == StepStatus.COMPLETED:
            st.success("Timeline rebuilt.")
        else:
            st.error(result.message or "Timeline rebuild failed.")

    if c_assets.button("Regenerate Missing Scene Assets", width="stretch"):
        regen = regenerate_missing_scene_assets(project_id)
        st.info(f"Missing assets report: {regen}")

    if c_rebuild.button("Rebuild Timeline from Disk Truth", width="stretch"):
        try:
            rebuilt_path = rebuild_timeline_from_disk(project_id)
            st.success(f"Timeline rebuilt from disk: {rebuilt_path}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Timeline rebuild failed: {exc}")

    if c_render.button("Render Final Video", width="stretch"):
        _persist_current_settings()
        result = run_render_video(project_id, pipeline_options)
        if result.status == StepStatus.COMPLETED:
            st.success("Render completed.")
            st.rerun()
        else:
            st.error(result.message or "Render failed.")

    failed_candidates = [step for step in PIPELINE_STEPS if state.step_statuses.get(step) == StepStatus.FAILED]
    default_reset_step = "script" if selected_mode == "topic_to_short_video" else "voiceover"
    reset_step = st.selectbox("Failed step to reset", options=failed_candidates or [default_reset_step], key=f"automation_reset_{project_id}")
    selected_downstream = st.selectbox("Reset downstream from", options=list(PIPELINE_STEPS), key=f"automation_downstream_{project_id}")
    r1, r2 = st.columns(2)
    if r1.button("Reset Failed Step", width="stretch", disabled=not failed_candidates):
        _reset_step(project_id, reset_step)
        st.success(f"Reset step: {reset_step}")

    if r2.button("Reset Downstream from Selected Step", width="stretch"):
        reset_downstream_steps(project_id, selected_downstream)
        st.success(f"Reset downstream from: {selected_downstream}")

    st.markdown("#### Render Preflight")
    preflight = preflight_report(project_id)
    payload = load_project_payload(project_id)
    selected_music_track = str(payload.get("selected_music_track", "") or "")
    music_resolution = resolve_music_track_for_project(project_id, selected_music_track)
    timeline_music_path = ""
    timeline_includes_music = False
    timeline_path = project_dir(project_id) / "timeline.json"
    if timeline_path.exists():
        timeline_payload = _load_json(timeline_path)
        if timeline_payload:
            meta = timeline_payload.get("meta", {}) if isinstance(timeline_payload.get("meta"), dict) else {}
            timeline_includes_music = bool(meta.get("include_music", False))
            music_payload = meta.get("music", {}) if isinstance(meta.get("music"), dict) else {}
            timeline_music_path = str(music_payload.get("path", "") or "")
    if preflight["ok"]:
        st.success("Preflight passed. Timeline/media references look healthy.")
    else:
        st.warning(f"Preflight found {preflight['issue_count']} issue(s).")
        if preflight["issues"].get("invalid_timeline_references"):
            st.error("Invalid timeline references detected:")
            for item in preflight["issues"]["invalid_timeline_references"]:
                st.code(str(item), language="text")
        expected_count = preflight.get("timeline_scene_count_expected")
        actual_count = preflight.get("timeline_scene_count_actual")
        if expected_count or actual_count:
            st.caption(f"Timeline scene count: expected={expected_count} actual={actual_count}")
        if preflight.get("timeline_rebuild_attempted") is not None:
            st.caption(
                "Timeline rebuild status: "
                f"attempted={preflight.get('timeline_rebuild_attempted', False)} "
                f"succeeded={preflight.get('timeline_rebuild_succeeded', False)}"
            )
    st.caption(
        "Music diagnostics · "
        f"selected={selected_music_track or 'none'} · "
        f"resolved={music_resolution.get('resolved_path', '') or 'none'} · "
        f"exists={music_resolution.get('file_exists', False)} · "
        f"copied_to_project={music_resolution.get('copied_to_project', False)} · "
        f"timeline_include_music={timeline_includes_music} · "
        f"timeline_music_path={timeline_music_path or 'none'}"
    )

    st.markdown("#### Logs")
    st.caption("Recent workflow log lines")
    st.code(_tail_file(workflow_log, lines=120) or "No workflow.log lines yet.", language="text")

    st.caption("Last render report")
    report_payload = _load_json(render_report)
    if report_payload:
        st.json(report_payload)
        st.caption(f"Post-run summary · final={report_payload.get('output_path', str(final_render))} | output_size={report_payload.get('resolved_output_size', 'unknown')} | subtitles={report_payload.get('subtitles_enabled', 'unknown')} (filter={report_payload.get('subtitle_filter_applied', 'unknown')}) | effects={report_payload.get('effect_style', 'unknown')} | music={report_payload.get('music_track', 'none')}")
    else:
        st.info("No render report found.")

    _render_post_run_video_section(project_id, final_render)
    _render_quick_scene_edits(project_id, scenes)
