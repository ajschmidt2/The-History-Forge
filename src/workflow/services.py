"""Reusable workflow step services callable from UI and automation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import hashlib
from pathlib import Path
from typing import Any

from src.ai_video_generation import generate_video
from src.audio import (
    TTS_PROVIDER_ELEVENLABS,
    TTS_PROVIDER_OPENAI,
    generate_voiceover_with_provider,
    resolve_tts_settings,
)
from src.storage import record_asset
import src.supabase_storage as _sb_store
from src.ui.timeline_sync import sync_timeline_for_project
from src.video.ffmpeg_render import render_video_from_timeline
from src.video.render_settings import normalize_aspect_ratio, normalize_video_effects_style, render_resolution_for_aspect_ratio
from src.video.timeline_builder import compute_scene_durations
from src.video.timeline_schema import Timeline
from src.video.utils import FFmpegNotFoundError, ensure_ffmpeg_exists, get_media_duration
from src.workflow.assets import preflight_report, resolve_music_track_for_project, sync_scene_asset_metadata
from src.workflow.models import StepStatus
from src.workflow.project_io import (
    ensure_project_files,
    load_project_payload,
    load_scenes,
    project_dir,
    save_project_payload,
    save_scenes,
)
from src.workflow.state import load_workflow_state, save_workflow_state, update_step_status
from utils import (
    generate_image_for_scene,
    generate_prompts_for_scenes,
    generate_script,
    generate_script_from_outline,
    generate_short_script,
    split_script_into_scenes,
)


@dataclass(slots=True)
class PipelineOptions:
    use_web_research: bool = False
    tone: str = "Documentary"
    audience: str = "General audience"
    number_of_scenes: int = 8
    variations_per_scene: int = 1
    aspect_ratio: str = "16:9"
    include_voiceover: bool = True
    include_music: bool = False
    visual_style: str = "Photorealistic cinematic"
    reading_level: str = "General"
    pacing: str = "Balanced"
    allow_silent_render: bool = False
    allow_captionless_render: bool = True
    include_subtitles: bool = True
    enable_video_effects: bool = True
    video_effects_style: str = "Ken Burns - Standard"
    selected_music_track: str = ""
    music_volume_relative_to_voiceover: float = 0.25
    scene_transition_type: str = "fade"
    voice_id: str = ""
    tts_provider: str = TTS_PROVIDER_ELEVENLABS
    elevenlabs_voice_id: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    openai_tts_instructions: str = ""
    automation_mode: str = "topic_to_short_video"
    topic: str = ""
    topic_direction: str = ""
    script_profile: str = "youtube_short_60s"
    ai_video_provider: str = "falai"
    image_provider: str = "falai"
    force_render_rebuild: bool = False


@dataclass(slots=True)
class FullWorkflowOptions:
    mode: str = "full_auto"
    overwrite_script: bool = False
    overwrite_scenes: bool = False
    overwrite_prompts: bool = False
    overwrite_images: bool = False
    overwrite_voiceover: bool = False
    overwrite_ai_video: bool = False
    overwrite_timeline: bool = False
    overwrite_render: bool = False
    enable_ai_video: bool = False
    ai_video_scene_indexes: list[int] = field(default_factory=list)
    hero_scene_indexes: list[int] = field(default_factory=list)
    ai_video_provider: str = "falai"
    ai_video_seconds: int = 8
    pipeline: PipelineOptions = field(default_factory=PipelineOptions)
    progress_callback: Any | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class FullWorkflowResult:
    project_id: str
    completed_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    failed_step: str = ""
    final_output_path: str = ""
    warnings: list[str] = field(default_factory=list)


def _workflow_logger(project_id: str) -> logging.Logger:
    log_path = project_dir(project_id) / "workflow.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"history_forge.workflow.{project_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(h, logging.FileHandler) and Path(h.baseFilename) == log_path.resolve() for h in logger.handlers):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _load_saved_voice_preference() -> str:
    preferences_path = Path("data/user_preferences.json")
    if not preferences_path.exists():
        return ""
    try:
        payload = json.loads(preferences_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("voice_id", "") or "").strip()


def _resolve_voice_id(project_id: str, options_voice_id: str, payload: dict[str, Any], logger: logging.Logger) -> str:
    selected_voice_id = str(options_voice_id or "").strip()
    if selected_voice_id:
        logger.info("voiceover setup: selected_voice_id=%s", selected_voice_id)
        return selected_voice_id

    payload_voice_id = str(payload.get("voice_id", "") or "").strip()
    if payload_voice_id:
        logger.info("voiceover setup: using project voice_id=%s", payload_voice_id)
        return payload_voice_id

    saved_voice_id = _load_saved_voice_preference()
    if saved_voice_id:
        logger.info("voiceover setup: using saved preference voice_id=%s", saved_voice_id)
        return saved_voice_id

    fallback = "r6YelDxIe1A40lDuW365"
    if fallback:
        logger.info("voiceover setup: using fallback DEFAULT_VOICE_ID=%s", fallback)
    return fallback


def _is_nonempty_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _scene_duration_fit_to_voiceover(project_id: str) -> StepResult:
    scenes = sync_scene_asset_metadata(project_id)
    if not scenes:
        return StepResult(project_id, "voiceover_timing", StepStatus.FAILED, message="No scenes available for timing sync.")

    voice_path = project_dir(project_id) / "assets/audio/voiceover.mp3"
    if not voice_path.exists():
        return StepResult(project_id, "voiceover_timing", StepStatus.FAILED, message="Voiceover file is missing.")

    payload = load_project_payload(project_id)
    try:
        voiceover_duration = float(get_media_duration(voice_path))
    except Exception as exc:  # noqa: BLE001
        return StepResult(project_id, "voiceover_timing", StepStatus.FAILED, message=str(exc))

    excerpts = [str(getattr(scene, "script_excerpt", "") or "") for scene in scenes]
    base_durations = compute_scene_durations(excerpts, wpm=float(payload.get("scene_wpm", 160) or 160), min_sec=1.5, max_sec=12.0)
    if not base_durations:
        return StepResult(project_id, "voiceover_timing", StepStatus.FAILED, message="Could not compute scene durations.")

    adjusted = list(base_durations)
    if voiceover_duration > 0 and sum(adjusted) > 0:
        scale = voiceover_duration / sum(adjusted)
        adjusted = [max(1.5, float(value) * scale) for value in adjusted]
        if sum(adjusted) > 0:
            correction = voiceover_duration / sum(adjusted)
            adjusted = [float(value) * correction for value in adjusted]

    for scene, duration in zip(scenes, adjusted):
        scene.estimated_duration_sec = float(duration)
    save_scenes(project_id, scenes)

    payload["estimated_total_runtime_sec"] = round(sum(adjusted), 2)
    save_project_payload(project_id, payload)
    return StepResult(project_id, "voiceover_timing", StepStatus.COMPLETED, outputs={"scene_count": len(scenes)})


def _ai_video_targets(scenes: list[Any], options: FullWorkflowOptions) -> list[Any]:
    selected = {int(i) for i in options.ai_video_scene_indexes if int(i) > 0}
    heroes = {int(i) for i in options.hero_scene_indexes if int(i) > 0}
    targets = selected or heroes
    return [scene for scene in scenes if int(getattr(scene, "index", 0) or 0) in targets]


def _run_ai_video_step(project_id: str, options: FullWorkflowOptions) -> StepResult:
    scenes = load_scenes(project_id)
    if not scenes:
        return StepResult(project_id, "ai_video", StepStatus.SKIPPED, message="No scenes available for AI video.")

    targets = _ai_video_targets(scenes, options)
    if not targets:
        return StepResult(project_id, "ai_video", StepStatus.SKIPPED, message="No selected hero scenes for AI video.")

    videos_dir = project_dir(project_id) / "assets/videos"
    warnings: list[str] = []
    generated = 0
    for scene in targets:
        prompt = str(
            getattr(scene, "video_prompt", "")
            or getattr(scene, "image_prompt", "")
            or getattr(scene, "visual_intent", "")
            or getattr(scene, "script_excerpt", "")
        ).strip()
        if not prompt:
            warnings.append(f"Scene {scene.index}: missing prompt; using image fallback.")
            continue
        try:
            _, local_path = generate_video(
                prompt=prompt,
                provider=options.ai_video_provider,
                project_id=project_id,
                aspect_ratio=options.pipeline.aspect_ratio,
                save_dir=videos_dir,
                seconds=options.ai_video_seconds,
            )
            if local_path and Path(local_path).exists():
                scene.video_path = str(Path(local_path))
                generated += 1
            else:
                warnings.append(f"Scene {scene.index}: provider returned no local video; using image fallback.")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Scene {scene.index}: AI video failed ({exc}); using image fallback.")

    save_scenes(project_id, scenes)
    sync_scene_asset_metadata(project_id, scenes)
    status = StepStatus.COMPLETED if generated > 0 else StepStatus.SKIPPED
    return StepResult(
        project_id,
        "ai_video",
        status,
        message="; ".join(warnings),
        outputs={"generated": generated, "warnings": warnings},
    )


def _step_outputs_exist(project_id: str, step: str) -> bool:
    project_path = project_dir(project_id)
    if step == "voiceover":
        return _is_nonempty_file(project_path / "assets/audio/voiceover.mp3")
    if step == "scenes":
        return bool(load_scenes(project_id))
    if step == "narrative":
        scenes = load_scenes(project_id)
        return bool(scenes) and all(bool(str(getattr(scene, "narration_text", "") or "").strip()) for scene in scenes)
    if step == "prompts":
        scenes = load_scenes(project_id)
        return bool(scenes) and all((getattr(scene, "image_prompt", "") or "").strip() for scene in scenes)
    if step == "images":
        scenes = load_scenes(project_id)
        if not scenes:
            return False
        images_dir = project_path / "assets/images"
        return all((images_dir / f"s{int(scene.index):02d}.png").exists() for scene in scenes)
    if step == "effects":
        payload = load_project_payload(project_id)
        return "enable_video_effects" in payload
    if step == "ai_video_clips":
        import tempfile
        tmp_clip_dir = Path(tempfile.gettempdir()) / f"ai_clips_{project_id}"
        return all((tmp_clip_dir / n).exists() for n in ("ai_clip_opening.mp4", "ai_clip_q2.mp4", "ai_clip_q3.mp4", "ai_clip_q4.mp4"))
    if step == "render":
        return (project_path / "renders/final.mp4").exists()
    return False


def _try_set_session_state(key: str, value: object) -> None:
    """Set a Streamlit session_state key when running inside a Streamlit runtime; no-op otherwise."""
    try:
        import streamlit as st
        st.session_state[key] = value
    except Exception:  # noqa: BLE001
        pass


def run_ai_video_clips(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    """Generate opening and midpoint AI video clips and persist paths to project payload."""
    import shutil
    import tempfile
    from src.video.ai_video_clips import generate_ai_video_clips

    _logger = _workflow_logger(project_id)
    aspect_ratio = (options.aspect_ratio if options else None) or "9:16"
    provider = (options.ai_video_provider if options else None) or "falai"

    # Allow session_state overrides from the Automation tab per-run settings.
    # In headless mode st.session_state exists but is inert; detect via ScriptRunContext.
    _session_state_live = False
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        _session_state_live = get_script_run_ctx() is not None
    except Exception:  # noqa: BLE001
        pass

    if _session_state_live:
        try:
            import streamlit as st
            ss_provider = st.session_state.get("automation_run_provider", "")
            if ss_provider:
                provider = ss_provider
            aspect_ratio = st.session_state.get("automation_clip_aspect_ratio", aspect_ratio)
            duration_seconds = st.session_state.get("automation_clip_duration", 5)
        except Exception:  # noqa: BLE001
            duration_seconds = 5
    else:
        duration_seconds = 5
        # Headless: read provider from daily_automation_settings.json if configured
        try:
            from src.workflow.daily_job import load_daily_automation_settings
            _settings = load_daily_automation_settings()
            _preset_provider = str(_settings.get("preset", {}).get("ai_video_provider", "") or "")
            if _preset_provider:
                provider = _preset_provider
            else:
                provider = "falai"
        except Exception:  # noqa: BLE001
            pass

    _logger.info(
        "ai_video_clips project=%s provider=%s aspect=%s duration=%ss",
        project_id, provider, aspect_ratio, duration_seconds,
    )

    def _clip_done(label: str, success: bool, done: int, total: int) -> None:
        """Called after each individual clip completes to push a live UI update."""
        _status = "done" if success else "failed"
        _logger.info(
            "ai_video_clips clip=%s status=%s progress=%d/%d",
            label, _status, done, total,
        )
        _msg = f"AI clip {done}/{total} — {label} {_status}"
        _try_set_session_state("ai_clips_progress", _msg)
        # Push an in-app toast notification so the user sees activity in real-time
        try:
            import streamlit as _st
            _icon = "✅" if success else "⚠️"
            _st.toast(f"{_icon} {_msg}", icon=None)
        except Exception:  # noqa: BLE001
            pass

    tmp_clip_dir = Path(tempfile.gettempdir()) / f"ai_clips_{project_id}"
    tmp_clip_dir.mkdir(exist_ok=True)

    try:
        opening_clip, q2_clip, q3_clip, q4_clip = generate_ai_video_clips(
            project_id=project_id,
            tmp_dir=tmp_clip_dir,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            provider=provider,
            workflow_logger=_logger,
            clip_done_callback=_clip_done,
        )
        _logger.info("ai_video_clips project=%s opening=%s q2=%s q3=%s q4=%s", project_id, opening_clip, q2_clip, q3_clip, q4_clip)

        # Persist clips to project assets dir so render can find them headlessly
        videos_dir = project_dir(project_id) / "assets" / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)

        def _persist(src, dest_name: str) -> "Path | None":
            if src and Path(src).exists() and Path(src).stat().st_size > 0:
                dest = videos_dir / dest_name
                shutil.copy2(src, dest)
                _logger.info("ai_video_clips persisted %s path=%s size=%d", dest_name, dest, dest.stat().st_size)
                return dest
            return None

        opening_persisted = _persist(opening_clip, "ai_opening_clip.mp4")
        q2_persisted      = _persist(q2_clip,      "ai_q2_clip.mp4")
        q3_persisted      = _persist(q3_clip,      "ai_q3_clip.mp4")
        q4_persisted      = _persist(q4_clip,      "ai_q4_clip.mp4")

        all_clips = [opening_persisted, q2_persisted, q3_persisted, q4_persisted]
        if not any(all_clips):
            _logger.warning("ai_video_clips no clips persisted provider=%s", provider)

        # Save to project payload for headless render access
        payload = load_project_payload(project_id)
        payload["ai_opening_clip_path"] = str(opening_persisted) if opening_persisted else ""
        payload["ai_q2_clip_path"]      = str(q2_persisted)      if q2_persisted      else ""
        payload["ai_q3_clip_path"]      = str(q3_persisted)      if q3_persisted      else ""
        payload["ai_q4_clip_path"]      = str(q4_persisted)      if q4_persisted      else ""
        save_project_payload(project_id, payload)

        # Also set session state for UI / Streamlit render path
        _try_set_session_state("auto_ai_opening_clip", str(opening_persisted) if opening_persisted else None)
        _try_set_session_state("auto_ai_q2_clip",      str(q2_persisted)      if q2_persisted      else None)
        _try_set_session_state("auto_ai_q3_clip",      str(q3_persisted)      if q3_persisted      else None)
        _try_set_session_state("auto_ai_q4_clip",      str(q4_persisted)      if q4_persisted      else None)

        generated = sum(1 for c in all_clips if c)
        return StepResult(
            project_id,
            "ai_video_clips",
            StepStatus.COMPLETED,
            message=f"Generated {generated}/4 AI video clips via {provider}",
            outputs={
                "opening_clip": str(opening_persisted) if opening_persisted else "",
                "q2_clip": str(q2_persisted) if q2_persisted else "",
                "q3_clip": str(q3_persisted) if q3_persisted else "",
                "q4_clip": str(q4_persisted) if q4_persisted else "",
            },
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("ai_video_clips project=%s failed: %s", project_id, exc)
        _try_set_session_state("auto_ai_opening_clip", None)
        _try_set_session_state("auto_ai_q2_clip", None)
        _try_set_session_state("auto_ai_q3_clip", None)
        _try_set_session_state("auto_ai_q4_clip", None)
        return StepResult(project_id, "ai_video_clips", StepStatus.FAILED, message=str(exc))


def run_full_workflow(project_id: str, options: FullWorkflowOptions | None = None) -> FullWorkflowResult:
    ensure_project_files(project_id)
    load_workflow_state(project_id)
    cfg = options or FullWorkflowOptions()
    logger = _workflow_logger(project_id)
    result = FullWorkflowResult(project_id=project_id)

    mode = str(cfg.mode or "full_auto").strip().lower()
    if mode not in {"full_auto", "resume_missing", "rerender_only"}:
        raise ValueError(f"Unsupported workflow mode: {cfg.mode}")

    progress = cfg.progress_callback if callable(cfg.progress_callback) else None

    payload = load_project_payload(project_id)
    automation_mode = str(cfg.pipeline.automation_mode or payload.get("automation_mode", "topic_to_short_video") or "topic_to_short_video").strip()
    is_topic_mode = automation_mode == "topic_to_short_video"
    logger.info("mode=%s", automation_mode)

    state = load_workflow_state(project_id)
    state.automation_mode = automation_mode
    state.topic = str(cfg.pipeline.topic or payload.get("topic", "") or "").strip()
    state.topic_direction = str(cfg.pipeline.topic_direction or payload.get("topic_direction", "") or "").strip()
    state.script_profile = str(cfg.pipeline.script_profile or payload.get("script_profile", "") or "").strip()
    save_workflow_state(project_id, state)

    if mode == "rerender_only":
        steps: list[tuple[str, bool, Any]] = [
            ("render", cfg.overwrite_render, lambda: run_render_video(project_id, cfg.pipeline)),
        ]
    else:
        steps = []
        if is_topic_mode:
            steps.append(("script", cfg.overwrite_script, lambda: run_generate_short_script(project_id, cfg.pipeline)))
        steps.extend([
            ("voiceover", cfg.overwrite_voiceover, lambda: run_generate_voiceover(project_id, cfg.pipeline)),
            ("scenes", cfg.overwrite_scenes, lambda: run_split_scenes(project_id, cfg.pipeline)),
            ("narrative", cfg.overwrite_scenes, lambda: run_apply_scene_narrative(project_id, cfg.pipeline)),
            ("prompts", cfg.overwrite_prompts, lambda: run_generate_prompts(project_id, cfg.pipeline)),
            ("images", cfg.overwrite_images, lambda: run_generate_images(project_id, cfg.pipeline)),
            ("effects", cfg.overwrite_timeline, lambda: run_apply_video_effects(project_id, cfg.pipeline)),
            ("ai_video_clips", False, lambda: run_ai_video_clips(project_id, cfg.pipeline)),
            ("render", cfg.overwrite_render, lambda: run_render_video(project_id, cfg.pipeline)),
        ])

    if mode != "rerender_only":
        if is_topic_mode:
            topic = str(cfg.pipeline.topic or payload.get("topic", "") or "").strip()
            if not topic:
                result.failed_step = "script"
                result.warnings.append("Topic is required before running topic-to-short-video automation.")
                logger.error("step=script status=failed error=missing_topic")
                if progress:
                    progress({"step": "script", "status": StepStatus.FAILED, "index": 1, "total": len(steps), "message": result.warnings[-1]})
                return result
        else:
            script_text = str(payload.get("script_text", "") or "").strip()
            if not script_text:
                result.failed_step = "voiceover"
                result.warnings.append("Script text is required before running full automation.")
                logger.error("step=voiceover status=failed error=missing_script")
                if progress:
                    progress({"step": "voiceover", "status": StepStatus.FAILED, "index": 1, "total": len(steps), "message": result.warnings[-1]})
                return result

    total = len(steps)
    for idx, (step_name, overwrite, handler) in enumerate(steps, start=1):
        if progress:
            progress({"step": step_name, "status": StepStatus.IN_PROGRESS, "index": idx, "total": total})

        if step_name == "voiceover" and not cfg.pipeline.include_voiceover:
            result.skipped_steps.append(step_name)
            logger.info("step=%s status=skipped reason=disabled", step_name)
            if progress:
                progress({"step": step_name, "status": StepStatus.SKIPPED, "index": idx, "total": total, "message": "disabled"})
            continue

        render_force_rebuild = step_name == "render" and bool(cfg.pipeline.force_render_rebuild)
        should_resume_existing = mode == "resume_missing" and not overwrite and not render_force_rebuild and _step_outputs_exist(project_id, step_name)
        if should_resume_existing:
            if step_name == "voiceover":
                result.completed_steps.append(step_name)
                logger.info("step=%s status=completed reason=reused_existing_output", step_name)
                if progress:
                    progress({"step": step_name, "status": StepStatus.COMPLETED, "index": idx, "total": total, "message": "reused_existing_output"})
            else:
                result.skipped_steps.append(step_name)
                logger.info("step=%s status=skipped reason=existing_outputs", step_name)
                if progress:
                    progress({"step": step_name, "status": StepStatus.SKIPPED, "index": idx, "total": total, "message": "existing_outputs"})
            continue

        if step_name == "script" and is_topic_mode:
            logger.info("step=script status=started profile=60s_short")
        elif step_name == "voiceover":
            logger.info("step=voiceover status=started provider=%s", cfg.pipeline.tts_provider)
        else:
            logger.info("step=%s status=started", step_name)
        step_result = handler()
        if step_result.status == StepStatus.FAILED:
            # ai_video_clips is non-fatal — log and continue without clips
            if step_name == "ai_video_clips":
                result.warnings.append(f"ai_video_clips skipped (non-fatal): {step_result.message}")
                result.skipped_steps.append(step_name)
                logger.warning("step=ai_video_clips status=failed reason=%s continuing_without_clips=True", step_result.message)
                if progress:
                    progress({"step": step_name, "status": StepStatus.SKIPPED, "index": idx, "total": total, "message": step_result.message})
                continue
            result.failed_step = step_name
            result.warnings.append(step_result.message)
            logger.error("step=%s status=failed error=%s", step_name, step_result.message)
            if progress:
                progress({"step": step_name, "status": StepStatus.FAILED, "index": idx, "total": total, "message": step_result.message})
            break

        if step_result.status == StepStatus.SKIPPED:
            result.skipped_steps.append(step_name)
            logger.info("step=%s status=skipped reason=%s", step_name, step_result.message or "no_op")
        else:
            result.completed_steps.append(step_name)
            if step_name == "script":
                logger.info("step=script status=completed word_count=%s", step_result.outputs.get("word_count", ""))
            else:
                logger.info("step=%s status=completed", step_name)

        if progress:
            progress({"step": step_name, "status": step_result.status, "index": idx, "total": total, "message": step_result.message})

    final_path = project_dir(project_id) / "renders/final.mp4"
    if final_path.exists():
        result.final_output_path = str(final_path)

    # Ensure YouTube metadata exists in the payload (for existing-script mode that
    # has no script step — the topic may already be in the payload but metadata not yet set).
    try:
        _yt_payload = load_project_payload(project_id)
        _yt_topic = str(_yt_payload.get("topic", "") or cfg.pipeline.topic or "").strip()
        if _yt_topic and not _yt_payload.get("youtube_title"):
            _yt_payload["youtube_title"] = f"{_yt_topic} #shorts #history"
            _yt_payload["youtube_description"] = (
                f"{_yt_topic}\n\nSubscribe to History Crossroads for more 60-second history stories!"
            )
            _yt_payload["youtube_tags"] = (
                [w.lower() for w in _yt_topic.split() if w.isalpha()]
                + ["history", "shorts", "historycrossroads", "historyfacts"]
            )
            save_project_payload(project_id, _yt_payload)
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "run_summary completed_steps=%s skipped_steps=%s failed_step=%s final_render=%s",
        ",".join(result.completed_steps),
        ",".join(result.skipped_steps),
        result.failed_step or "",
        result.final_output_path or "",
    )
    return result


@dataclass(slots=True)
class StepResult:
    project_id: str
    step: str
    status: StepStatus
    message: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)


def _safe_setting_bool(raw_value: Any, fallback: bool) -> bool:
    if raw_value is None:
        return fallback
    if isinstance(raw_value, str):
        lowered = raw_value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return bool(raw_value)


def _state_get(state: Any, key: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


@dataclass(slots=True, frozen=True)
class ResolvedAutomationRenderSettings:
    aspect_ratio: str
    output_width: int
    output_height: int
    output_size: str
    subtitles_enabled: bool
    effects_style: str
    music_enabled: bool
    music_track: str


def resolve_automation_render_settings(
    project_id: str,
    workflow_state: dict[str, Any],
    project_state: dict[str, Any],
    session_state: dict[str, Any] | None = None,
) -> ResolvedAutomationRenderSettings:
    ratio = normalize_aspect_ratio(
        (session_state or {}).get("aspect_ratio", _state_get(workflow_state, "aspect_ratio", _state_get(project_state, "aspect_ratio", "16:9"))),
        default="16:9",
    )
    output_size = render_resolution_for_aspect_ratio(ratio)
    width_str, height_str = output_size.split("x", maxsplit=1)

    raw_effects_enabled = (session_state or {}).get(
        "enable_video_effects",
        _state_get(workflow_state, "enable_video_effects", _state_get(project_state, "enable_video_effects", True)),
    )
    effects_enabled = _safe_setting_bool(raw_effects_enabled, True)
    effect_style = normalize_video_effects_style(
        (session_state or {}).get("video_effects_style", _state_get(workflow_state, "video_effects_style", _state_get(project_state, "video_effects_style", "Ken Burns - Standard"))),
        enable_motion=effects_enabled,
    )

    subtitles_enabled = _safe_setting_bool(
        (session_state or {}).get(
            "enable_subtitles",
            _state_get(workflow_state, "enable_subtitles", _state_get(project_state, "enable_subtitles", True)),
        ),
        True,
    )
    music_enabled = _safe_setting_bool(
        (session_state or {}).get("enable_music", _state_get(workflow_state, "enable_music", _state_get(project_state, "enable_music", False))),
        False,
    )
    music_track = str(
        (session_state or {}).get(
            "selected_music_track",
            _state_get(workflow_state, "selected_music_track", _state_get(project_state, "selected_music_track", "")),
        )
        or ""
    ).strip()

    return ResolvedAutomationRenderSettings(
        aspect_ratio=ratio,
        output_width=int(width_str),
        output_height=int(height_str),
        output_size=output_size,
        subtitles_enabled=subtitles_enabled,
        effects_style=effect_style,
        music_enabled=music_enabled,
        music_track=music_track if music_enabled else "",
    )


def should_apply_subtitles(resolved_settings: ResolvedAutomationRenderSettings, timeline_meta: Any | None) -> bool:
    return bool(resolved_settings.subtitles_enabled)


def _load_options(project_id: str, options: PipelineOptions | None) -> tuple[dict[str, Any], PipelineOptions]:
    payload = load_project_payload(project_id)
    merged = options or PipelineOptions()
    options_provided = options is not None

    def _safe_int(raw_value: Any, fallback: int) -> int:
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return fallback

    def _safe_bool(raw_value: Any, fallback: bool) -> bool:
        if raw_value is None:
            return fallback
        if isinstance(raw_value, str):
            lowered = raw_value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        return bool(raw_value)

    def _safe_aspect_ratio(raw_value: Any, fallback: str) -> str:
        ratio = str(raw_value or fallback).strip()
        return ratio if ratio in {"16:9", "9:16"} else fallback

    merged.tone = payload.get("tone", merged.tone) or merged.tone
    merged.audience = payload.get("audience", merged.audience) or merged.audience
    if options_provided:
        scene_count = _safe_int(merged.number_of_scenes or payload.get("scene_count", payload.get("max_scenes", 8)) or 8, 8)
    else:
        scene_count = _safe_int(payload.get("scene_count", payload.get("max_scenes", merged.number_of_scenes)) or merged.number_of_scenes, int(merged.number_of_scenes or 8))
    merged.number_of_scenes = max(1, min(75, scene_count))
    merged.variations_per_scene = max(1, _safe_int(payload.get("variations_per_scene", merged.variations_per_scene) or merged.variations_per_scene, int(merged.variations_per_scene or 1)))
    # For render/output settings, explicit pipeline_options take priority over the saved payload.
    # When options_provided=False (no caller-supplied options), fall back to the payload as before.
    merged.aspect_ratio = _safe_aspect_ratio(
        merged.aspect_ratio if options_provided else payload.get("aspect_ratio", merged.aspect_ratio),
        merged.aspect_ratio,
    )
    merged.visual_style = payload.get("visual_style", merged.visual_style) or merged.visual_style
    merged.reading_level = payload.get("reading_level", merged.reading_level) or merged.reading_level
    merged.pacing = payload.get("pacing", merged.pacing) or merged.pacing
    merged.include_voiceover = _safe_bool(
        merged.include_voiceover if options_provided else payload.get("automation_generate_voiceover", payload.get("include_voiceover", merged.include_voiceover)),
        merged.include_voiceover,
    )
    merged.include_music = _safe_bool(
        merged.include_music if options_provided else payload.get("enable_music", payload.get("include_music", merged.include_music)),
        merged.include_music,
    )
    merged.include_subtitles = _safe_bool(
        merged.include_subtitles if options_provided else payload.get("enable_subtitles", payload.get("automation_include_captions", merged.include_subtitles)),
        merged.include_subtitles,
    )
    merged.enable_video_effects = _safe_bool(
        merged.enable_video_effects if options_provided else payload.get("enable_video_effects", merged.enable_video_effects),
        merged.enable_video_effects,
    )
    merged.video_effects_style = normalize_video_effects_style(
        merged.video_effects_style if options_provided else payload.get("video_effects_style", merged.video_effects_style),
        enable_motion=merged.enable_video_effects,
    )
    merged.selected_music_track = str(
        merged.selected_music_track if options_provided else (payload.get("selected_music_track", merged.selected_music_track) or "")
    )
    try:
        music_level = float(
            merged.music_volume_relative_to_voiceover if options_provided
            else (payload.get("music_volume_relative_to_voiceover", merged.music_volume_relative_to_voiceover) or merged.music_volume_relative_to_voiceover)
        )
    except (TypeError, ValueError):
        music_level = 0.25
    merged.music_volume_relative_to_voiceover = min(1.0, max(0.0, music_level))
    _allowed_transitions = {"random", "fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "slideright", "smoothleft", "smoothright", "circleopen", "circleclose", "distance"}
    _raw_transition = str(
        merged.scene_transition_type if options_provided else payload.get("scene_transition_type", merged.scene_transition_type)
    ).strip().lower()
    merged.scene_transition_type = _raw_transition if _raw_transition in _allowed_transitions else "fade"
    merged.voice_id = str(merged.voice_id or payload.get("voice_id", "") or "").strip()
    merged.tts_provider = str(merged.tts_provider or payload.get("tts_provider", TTS_PROVIDER_ELEVENLABS) or TTS_PROVIDER_ELEVENLABS).strip().lower()
    merged.elevenlabs_voice_id = str(merged.elevenlabs_voice_id or merged.voice_id or payload.get("elevenlabs_voice_id", payload.get("voice_id", "")) or "").strip()
    merged.openai_tts_model = str(merged.openai_tts_model or payload.get("openai_tts_model", "gpt-4o-mini-tts") or "gpt-4o-mini-tts").strip()
    merged.openai_tts_voice = str(merged.openai_tts_voice or payload.get("openai_tts_voice", "alloy") or "alloy").strip()
    merged.openai_tts_instructions = str(merged.openai_tts_instructions or payload.get("openai_tts_instructions", "") or "").strip()
    merged.automation_mode = str(merged.automation_mode or payload.get("automation_mode", "topic_to_short_video") or "topic_to_short_video").strip()
    merged.topic = str(merged.topic or payload.get("topic", "") or "").strip()
    merged.topic_direction = str(merged.topic_direction or payload.get("topic_direction", payload.get("story_angle", "")) or "").strip()
    merged.script_profile = str(merged.script_profile or payload.get("script_profile", "youtube_short_60s") or "youtube_short_60s").strip()
    return payload, merged


def run_generate_script(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    load_workflow_state(project_id)
    payload, cfg = _load_options(project_id, options)
    existing_script = str(payload.get("script_text", "") or "").strip()
    if existing_script:
        script_path = project_dir(project_id) / "script.txt"
        if not script_path.exists() or not script_path.read_text(encoding="utf-8").strip():
            script_path.write_text(existing_script, encoding="utf-8")
        return StepResult(project_id, "script", StepStatus.SKIPPED, message="Existing script text found.")

    topic = str(payload.get("topic", "") or "").strip()
    if not topic:
        return StepResult(project_id, "script", StepStatus.FAILED, message="Project topic is empty.")

    update_step_status(project_id, "script", StepStatus.IN_PROGRESS)
    try:
        outline_raw = payload.get("outline_json_text", "")
        if isinstance(outline_raw, str) and outline_raw.strip().startswith("{"):
            import json
            outline = json.loads(outline_raw)
            script_text = generate_script_from_outline(
                outline=outline,
                tone=cfg.tone,
                reading_level=cfg.reading_level,
                pacing=cfg.pacing,
                desired_scenes=cfg.number_of_scenes,
            )
        else:
            brief = payload.get("research_brief_text", "") if payload.get("use_research_brief_for_script") else ""
            script_text = generate_script(
                topic=topic,
                length=str(payload.get("length", "8–10 minutes") or "8–10 minutes"),
                tone=cfg.tone,
                audience=cfg.audience,
                angle=str(payload.get("story_angle", "Balanced overview") or "Balanced overview"),
                research_brief=str(brief or ""),
                desired_scenes=cfg.number_of_scenes,
            )
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "script", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "script", StepStatus.FAILED, message=str(exc))

    payload["script_text"] = script_text
    (project_dir(project_id) / "script.txt").write_text(script_text, encoding="utf-8")
    save_project_payload(project_id, payload)
    try:
        _sb_store.upload_script(project_id, script_text)
    except Exception:
        pass
    update_step_status(project_id, "script", StepStatus.COMPLETED)
    return StepResult(project_id, "script", StepStatus.COMPLETED, outputs={"script_text": script_text})




def run_generate_short_script(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    load_workflow_state(project_id)
    payload, cfg = _load_options(project_id, options)

    topic = str(cfg.topic or payload.get("topic", "") or "").strip()
    if not topic:
        return StepResult(project_id, "script", StepStatus.FAILED, message="Project topic is empty.")

    update_step_status(project_id, "script", StepStatus.IN_PROGRESS)
    try:
        script_text = generate_short_script(
            topic=topic,
            tone=cfg.tone,
            reading_level=cfg.reading_level,
            direction=cfg.topic_direction,
        )
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "script", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "script", StepStatus.FAILED, message=str(exc))

    payload["topic"] = topic
    payload["topic_direction"] = str(cfg.topic_direction or "").strip()
    payload["script_profile"] = "youtube_short_60s"
    payload["automation_mode"] = "topic_to_short_video"
    payload["script_text"] = script_text
    # Pre-generate YouTube metadata so the upload tab can auto-fill
    payload["youtube_title"] = f"{topic} #shorts #history"
    payload["youtube_description"] = (
        f"{topic}\n\nSubscribe to History Crossroads for more 60-second history stories!"
    )
    payload["youtube_tags"] = (
        [w.lower() for w in topic.split() if w.isalpha()]
        + ["history", "shorts", "historycrossroads", "historyfacts"]
    )
    (project_dir(project_id) / "script.txt").write_text(script_text, encoding="utf-8")
    save_project_payload(project_id, payload)
    try:
        _sb_store.upload_script(project_id, script_text)
    except Exception:
        pass

    update_step_status(project_id, "script", StepStatus.COMPLETED)
    word_count = len([w for w in script_text.split() if w.strip()])
    return StepResult(project_id, "script", StepStatus.COMPLETED, outputs={"script_text": script_text, "word_count": word_count})


def run_split_scenes(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    script_text = str(payload.get("script_text", "") or "").strip()
    if not script_text:
        return StepResult(project_id, "scenes", StepStatus.FAILED, message="Script text is missing.")

    update_step_status(project_id, "scenes", StepStatus.IN_PROGRESS)
    requested_scene_count = max(1, min(int(cfg.number_of_scenes or 8), 75))
    try:
        scenes = split_script_into_scenes(script_text, max_scenes=requested_scene_count, wpm=int(payload.get("scene_wpm", 160) or 160))
        if len(scenes) != requested_scene_count:
            raise ValueError(f"Scene splitter returned {len(scenes)} scenes; expected {requested_scene_count}.")
        save_scenes(project_id, scenes)

        sync_scene_asset_metadata(project_id, scenes)
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "scenes", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "scenes", StepStatus.FAILED, message=str(exc))

    update_step_status(project_id, "scenes", StepStatus.COMPLETED)
    return StepResult(project_id, "scenes", StepStatus.COMPLETED, outputs={"scene_count": len(scenes)})



def run_apply_scene_narrative(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    scenes = load_scenes(project_id)
    if not scenes:
        return StepResult(project_id, "narrative", StepStatus.FAILED, message="No scenes available.")

    update_step_status(project_id, "narrative", StepStatus.IN_PROGRESS)
    try:
        for scene in scenes:
            excerpt = str(getattr(scene, "script_excerpt", "") or "").strip()
            title = str(getattr(scene, "title", "") or "").strip()
            narration = excerpt or title or f"Scene {int(getattr(scene, 'index', 0) or 0)}"
            scene.narration_text = narration
            scene.subtitle_text = narration
        save_scenes(project_id, scenes)
        sync_scene_asset_metadata(project_id, scenes)
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "narrative", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "narrative", StepStatus.FAILED, message=str(exc))

    update_step_status(project_id, "narrative", StepStatus.COMPLETED)
    return StepResult(project_id, "narrative", StepStatus.COMPLETED, outputs={"scene_count": len(scenes)})


def run_apply_video_effects(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    payload["enable_video_effects"] = bool(cfg.enable_video_effects)
    payload["video_effects_style"] = normalize_video_effects_style(cfg.video_effects_style, enable_motion=cfg.enable_video_effects)
    payload["scene_transition_type"] = cfg.scene_transition_type
    save_project_payload(project_id, payload)
    update_step_status(project_id, "effects", StepStatus.COMPLETED)
    return StepResult(project_id, "effects", StepStatus.COMPLETED, outputs={"enable_video_effects": bool(cfg.enable_video_effects), "video_effects_style": normalize_video_effects_style(cfg.video_effects_style, enable_motion=cfg.enable_video_effects)})

def run_generate_prompts(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    scenes = load_scenes(project_id)
    if not scenes:
        return StepResult(project_id, "prompts", StepStatus.FAILED, message="No scenes available.")

    update_step_status(project_id, "prompts", StepStatus.IN_PROGRESS)
    try:
        for scene in scenes:
            narration_text = str(getattr(scene, "narration_text", "") or getattr(scene, "subtitle_text", "") or getattr(scene, "script_excerpt", "") or "").strip()
            scene.narration_text = narration_text
        scenes = generate_prompts_for_scenes(
            scenes,
            tone=cfg.tone,
            style=cfg.visual_style,
            characters=payload.get("character_registry", []),
            objects=payload.get("object_registry", []),
        )
        for scene in scenes:
            scene_title = str(getattr(scene, "title", "") or "").strip()
            scene_excerpt = str(getattr(scene, "script_excerpt", "") or "").strip()
            scene_visual_intent = str(getattr(scene, "visual_intent", "") or "").strip()
            narration_text = str(getattr(scene, "narration_text", "") or scene_excerpt).strip()
            base_prompt = str(getattr(scene, "image_prompt", "") or "").strip()
            if base_prompt:
                scene.image_prompt = (
                    f"{base_prompt}\n"
                    f"Scene title: {scene_title}\n"
                    f"Script anchor excerpt: {scene_excerpt}\n"
                    f"Narration context: {narration_text}\n"
                    f"Visual intent: {scene_visual_intent}\n"
                    f"Aspect ratio: {cfg.aspect_ratio}."
                ).strip()
            scene.video_prompt = str(getattr(scene, "video_prompt", "") or "").strip()
            scene.prompt_spec = dict(getattr(scene, "prompt_spec", {}) or {})
            scene.prompt_spec["final_output"] = {
                "scene_id": str(getattr(scene, "scene_id", "") or f"scene-{scene.index}"),
                "image_prompt": scene.image_prompt,
                "video_prompt": scene.video_prompt,
                "negative_prompt": str(getattr(scene, "negative_prompt", "") or ""),
                "scene_summary": str(getattr(scene, "scene_summary", "") or ""),
                "continuity_notes": str(getattr(scene, "continuity_notes", "") or ""),
                "scores": dict(getattr(scene, "prompt_scores", {}) or {}),
            }
        save_scenes(project_id, scenes)
        sync_scene_asset_metadata(project_id, scenes)
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "prompts", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "prompts", StepStatus.FAILED, message=str(exc))

    update_step_status(project_id, "prompts", StepStatus.COMPLETED)
    return StepResult(project_id, "prompts", StepStatus.COMPLETED, outputs={"scene_count": len(scenes)})


def run_generate_images(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    _, cfg = _load_options(project_id, options)
    scenes = load_scenes(project_id)
    if not scenes:
        return StepResult(project_id, "images", StepStatus.FAILED, message="No scenes available.")

    if any(not (getattr(scene, "image_prompt", "") or "").strip() for scene in scenes):
        run_generate_prompts(project_id, cfg)
        scenes = load_scenes(project_id)

    update_step_status(project_id, "images", StepStatus.IN_PROGRESS)
    images_dir = project_dir(project_id) / "assets/images"
    generated = 0
    logger = _workflow_logger(project_id)
    scenes_to_generate = scenes[: cfg.number_of_scenes]
    # Build a cross-scene visual anchor from project metadata so all images share
    # the same era, palette, and cinematic treatment.
    payload = load_project_payload(project_id)
    _topic = str(payload.get("topic", "") or payload.get("project_title", "") or "").strip()
    _era = str(payload.get("era", "") or "").strip()
    visual_anchor = ""
    if _topic:
        visual_anchor = f"{_topic}. {_era + '. ' if _era else ''}Consistent historical era, unified palette, same cinematic treatment across all scenes."
    try:
        for scene in scenes_to_generate:
            updated = generate_image_for_scene(
                scene,
                aspect_ratio=cfg.aspect_ratio,
                visual_style=cfg.visual_style,
                visual_anchor=visual_anchor,
                provider=getattr(cfg, "image_provider", "falai") or "falai",
            )
            if updated.image_bytes:
                out = images_dir / f"s{updated.index:02d}.png"
                out.write_bytes(updated.image_bytes)
                record_asset(project_id, "image", out)
                try:
                    _sb_store.upload_image(project_id, out.name, out)
                except Exception:
                    pass
                generated += 1
            else:
                err = getattr(updated, "image_error", "") or "unknown error"
                logger.warning("image_generation_failed scene=%s error=%s", getattr(updated, "index", "?"), err)
        save_scenes(project_id, scenes)
        sync_scene_asset_metadata(project_id, scenes)
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "images", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "images", StepStatus.FAILED, message=str(exc), outputs={"generated": generated})

    expected_count = len(scenes_to_generate)
    if generated < expected_count:
        missing = expected_count - generated
        msg = f"{missing}/{expected_count} scene images failed to generate"
        update_step_status(project_id, "images", StepStatus.FAILED, error=msg)
        return StepResult(project_id, "images", StepStatus.FAILED, message=msg, outputs={"generated": generated})

    update_step_status(project_id, "images", StepStatus.COMPLETED)
    return StepResult(project_id, "images", StepStatus.COMPLETED, outputs={"generated": generated})


def run_generate_voiceover(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    logger = _workflow_logger(project_id)
    if not cfg.include_voiceover:
        return StepResult(project_id, "voiceover", StepStatus.SKIPPED, message="Voiceover is disabled.")

    script_text = str(payload.get("script_text", "") or "").strip()
    if not script_text:
        return StepResult(project_id, "voiceover", StepStatus.FAILED, message="Script text is missing.")

    output_path = project_dir(project_id) / "assets/audio/voiceover.mp3"
    tts_settings = resolve_tts_settings(
        payload,
        tts_provider=cfg.tts_provider,
        elevenlabs_voice_id=cfg.elevenlabs_voice_id or cfg.voice_id,
        openai_tts_model=cfg.openai_tts_model,
        openai_tts_voice=cfg.openai_tts_voice,
        openai_tts_instructions=cfg.openai_tts_instructions,
        output_format="mp3",
    )

    if tts_settings.provider == TTS_PROVIDER_ELEVENLABS and not tts_settings.elevenlabs_voice_id:
        tts_settings.elevenlabs_voice_id = _resolve_voice_id(project_id, cfg.voice_id, payload, logger)

    logger.info("voiceover setup: script_detected=%s output_path=%s provider=%s", bool(script_text), output_path, tts_settings.provider)
    logger.info(
        "step=voiceover status=started provider=%s model=%s voice=%s response_format=%s output_path=%s",
        tts_settings.provider,
        tts_settings.openai_tts_model,
        tts_settings.openai_tts_voice,
        tts_settings.output_format,
        output_path,
    )

    if tts_settings.provider == TTS_PROVIDER_ELEVENLABS and not tts_settings.elevenlabs_voice_id:
        if cfg.allow_silent_render:
            return StepResult(
                project_id,
                "voiceover",
                StepStatus.SKIPPED,
                message="Voice ID is missing; skipping voiceover because silent render is enabled.",
            )
        return StepResult(project_id, "voiceover", StepStatus.FAILED, message="Voice ID is required.")

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("ab"):
            pass
    except OSError as exc:
        return StepResult(project_id, "voiceover", StepStatus.FAILED, message=f"Voiceover output path is not writable: {exc}")

    update_step_status(project_id, "voiceover", StepStatus.IN_PROGRESS)
    try:
        audio, err = generate_voiceover_with_provider(script_text, tts_settings, output_path=output_path if tts_settings.provider == TTS_PROVIDER_OPENAI else None)
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "voiceover", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "voiceover", StepStatus.FAILED, message=str(exc))

    if err or not audio:
        message = str(err or "Voiceover generation failed")
        update_step_status(project_id, "voiceover", StepStatus.FAILED, error=message)
        return StepResult(project_id, "voiceover", StepStatus.FAILED, message=message)

    output_path.write_bytes(audio)
    record_asset(project_id, "voiceover", output_path)
    try:
        _sb_store.upload_audio(project_id, output_path.name, output_path)
    except Exception:
        pass

    payload["tts_provider"] = tts_settings.provider
    payload["elevenlabs_voice_id"] = tts_settings.elevenlabs_voice_id
    payload["openai_tts_model"] = tts_settings.openai_tts_model
    payload["openai_tts_voice"] = tts_settings.openai_tts_voice
    payload["openai_tts_instructions"] = tts_settings.openai_tts_instructions
    payload["voice_id"] = tts_settings.elevenlabs_voice_id or payload.get("voice_id", "")
    save_project_payload(project_id, payload)

    if tts_settings.provider == TTS_PROVIDER_OPENAI:
        logger.info(
            "voiceover provider=openai model=%s voice=%s output_path=%s",
            tts_settings.openai_tts_model,
            tts_settings.openai_tts_voice,
            output_path,
        )
    else:
        logger.info("voiceover provider=elevenlabs voice_id=%s output_path=%s", tts_settings.elevenlabs_voice_id, output_path)

    update_step_status(project_id, "voiceover", StepStatus.COMPLETED)
    return StepResult(
        project_id,
        "voiceover",
        StepStatus.COMPLETED,
        outputs={
            "voiceover_path": str(output_path),
            "provider": tts_settings.provider,
            "voice_id": tts_settings.elevenlabs_voice_id,
            "openai_tts_model": tts_settings.openai_tts_model,
            "openai_tts_voice": tts_settings.openai_tts_voice,
        },
    )



def _automation_settings_payload(cfg: PipelineOptions) -> dict[str, Any]:
    return {
        "aspect_ratio": normalize_aspect_ratio(cfg.aspect_ratio, default="16:9"),
        "subtitles": bool(cfg.include_subtitles),
        "effects_enabled": bool(cfg.enable_video_effects),
        "effects_style": normalize_video_effects_style(cfg.video_effects_style, enable_motion=cfg.enable_video_effects),
        "music_enabled": bool(cfg.include_music),
        "music_track": str(cfg.selected_music_track or "").strip(),
        "music_volume": round(float(cfg.music_volume_relative_to_voiceover), 4),
    }


def _automation_settings_signature(cfg: PipelineOptions) -> str:
    payload = _automation_settings_payload(cfg)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _invalidate_render_artifacts_for_settings_change(project_id: str, old_sig: str, new_sig: str, logger: logging.Logger, change_reasons: list[str] | None = None) -> None:
    if not old_sig or old_sig == new_sig:
        logger.info("settings_fingerprint_changed=False")
        return
    base = project_dir(project_id)
    paths = [
        base / "timeline.json",
        base / "renders" / "final.mp4",
        base / "renders" / "render_report.json",
        base / "renders" / "captions.ass",
        base / "renders" / "captions.srt",
        base / "scene_cache",
    ]
    for path in paths:
        try:
            if path.is_dir():
                import shutil
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        except Exception:
            pass
    for reason in (change_reasons or ["settings_changed"]):
        logger.info("invalidating_scene_cache reason=%s", reason)
        logger.info("invalidating_render reason=%s", reason)
    logger.info("settings_fingerprint_changed=True old_signature=%s new_signature=%s rebuild_triggered=true", old_sig, new_sig)



# Keep only well-tested xfade transition types (circleopen/close, distance,
# smoothleft/right, and dissolve variants are inconsistently supported across
# FFmpeg builds and can cause the entire crossfade graph to fail silently).
_NAMED_TRANSITIONS = ["fade", "fadeblack", "wipeleft", "wiperight", "slideleft", "slideright"]


def _build_transition_types(transition_type: str, scene_count: int) -> list[str]:
    import random as _random
    if transition_type == "random":
        return [_random.choice(_NAMED_TRANSITIONS) for _ in range(max(0, scene_count - 1))]
    safe = transition_type if transition_type in _NAMED_TRANSITIONS else "fade"
    return [safe] * max(0, scene_count - 1)


def run_sync_timeline(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    logger = _workflow_logger(project_id)
    workflow_state = load_workflow_state(project_id)
    session_settings = {
        "aspect_ratio": cfg.aspect_ratio,
        "enable_subtitles": cfg.include_subtitles,
        "enable_video_effects": cfg.enable_video_effects,
        "video_effects_style": cfg.video_effects_style,
        "enable_music": cfg.include_music,
        "selected_music_track": cfg.selected_music_track,
    }
    resolved_settings = resolve_automation_render_settings(project_id, workflow_state, payload, session_settings)
    logger.info(
        "automation_resolved_settings aspect_ratio=%s output_size=%sx%s subtitles_enabled=%s effects_style=%s music_enabled=%s music_track=%s",
        resolved_settings.aspect_ratio,
        resolved_settings.output_width,
        resolved_settings.output_height,
        resolved_settings.subtitles_enabled,
        resolved_settings.effects_style,
        resolved_settings.music_enabled,
        resolved_settings.music_track,
    )
    music_resolution = resolve_music_track_for_project(project_id, resolved_settings.music_track) if resolved_settings.music_enabled else {
        "selected_track": "",
        "resolved_path": "",
        "copied_to_project": False,
        "file_exists": False,
    }
    resolved_music_track = str(music_resolution.get("resolved_path", "") or "")
    logger.info(
        "music_track_selected=%s music_track_resolved=%s music_track_copied_to_project=%s music_track_exists=%s",
        music_resolution.get("selected_track", ""),
        resolved_music_track,
        bool(music_resolution.get("copied_to_project", False)),
        bool(music_resolution.get("file_exists", False)),
    )
    if resolved_settings.music_enabled and not resolved_music_track:
        return StepResult(project_id, "timeline", StepStatus.FAILED, message="Background music is enabled but selected track could not be resolved.")

    settings_payload = _automation_settings_payload(cfg)
    previous_settings_payload = payload.get("automation_settings_payload", {}) or {}
    changed_keys = [
        key for key in ["aspect_ratio", "subtitles", "effects_style", "music_enabled", "music_track"]
        if previous_settings_payload.get(key) != settings_payload.get(key)
    ]
    new_settings_signature = _automation_settings_signature(cfg)
    previous_settings_signature = str(payload.get("automation_settings_signature", "") or "").strip()
    _invalidate_render_artifacts_for_settings_change(
        project_id,
        previous_settings_signature,
        new_settings_signature,
        logger,
        change_reasons=[f"{key}_changed" for key in changed_keys] or None,
    )
    payload["automation_settings_signature"] = new_settings_signature
    payload["automation_settings_payload"] = settings_payload
    save_project_payload(project_id, payload)
    scenes = sync_scene_asset_metadata(project_id)
    if not scenes:
        return StepResult(project_id, "timeline", StepStatus.FAILED, message="No scenes available.")

    if any(not (getattr(scene, "image_prompt", "") or "").strip() for scene in scenes):
        run_generate_prompts(project_id, cfg)
        scenes = load_scenes(project_id)

    voice_path = project_dir(project_id) / "assets/audio/voiceover.mp3"
    if voice_path.exists():
        # Recompute scene durations proportional to word count, scaled to voiceover duration.
        # This ensures each scene's display time matches its share of the narration text.
        timing_result = _scene_duration_fit_to_voiceover(project_id)
        if timing_result.status == StepStatus.COMPLETED:
            scenes = load_scenes(project_id)
    elif any(float(getattr(scene, "estimated_duration_sec", 0.0) or 0.0) <= 0 for scene in scenes):
        excerpts = [str(getattr(scene, "script_excerpt", "") or "") for scene in scenes]
        durations = compute_scene_durations(excerpts, wpm=float(payload.get("scene_wpm", 160) or 160))
        for scene, duration in zip(scenes, durations):
            scene.estimated_duration_sec = float(duration)
        save_scenes(project_id, scenes)

    update_step_status(project_id, "timeline", StepStatus.IN_PROGRESS)
    project_path = project_dir(project_id)
    scene_captions = [str(getattr(scene, "subtitle_text", "") or getattr(scene, "narration_text", "") or getattr(scene, "script_excerpt", "") or "") for scene in scenes]
    # Base attenuation ensures music stays well below a loudnorm'd voiceover
    # (-16 LUFS) even when the slider is at maximum.  The slider ratio maps
    # linearly in dB from this base: ratio=1.0 → -12 dB, ratio=0.25 → -24 dB.
    _MUSIC_BASE_ATTENUATION_DB = -12.0
    music_volume_db = _MUSIC_BASE_ATTENUATION_DB
    try:
        import math
        ratio = max(0.0, min(1.0, float(cfg.music_volume_relative_to_voiceover)))
        if ratio > 0:
            music_volume_db = round(20.0 * math.log10(ratio) + _MUSIC_BASE_ATTENUATION_DB, 2)
        else:
            music_volume_db = -96.0  # effectively muted
    except Exception:
        music_volume_db = _MUSIC_BASE_ATTENUATION_DB
    logger.info(
        "automation_resolved_settings aspect_ratio=%s output_size=%sx%s subtitles_enabled=%s effects_style=%s music_enabled=%s music_track=%s",
        resolved_settings.aspect_ratio,
        resolved_settings.output_width,
        resolved_settings.output_height,
        resolved_settings.subtitles_enabled,
        resolved_settings.effects_style,
        resolved_settings.music_enabled,
        resolved_settings.music_track,
    )
    try:
        timeline_path = sync_timeline_for_project(
            project_path=project_path,
            project_id=project_id,
            title=str(payload.get("project_title", project_id) or project_id),
            session_scenes=scenes,
            scene_captions=scene_captions,
            meta_overrides={
                "aspect_ratio": resolved_settings.aspect_ratio,
                "include_voiceover": cfg.include_voiceover,
                "include_music": resolved_settings.music_enabled,
                "burn_captions": resolved_settings.subtitles_enabled,
                "enable_motion": cfg.enable_video_effects,
                "video_effects_style": resolved_settings.effects_style,
                "resolution": resolved_settings.output_size,
                "selected_music_track": resolved_music_track,
                "music": {"path": resolved_music_track, "volume_db": music_volume_db, "ducking": {"enabled": True, "threshold_db": -28, "ratio": 8, "attack": 15, "release": 250}},
                "transition_types": _build_transition_types(cfg.scene_transition_type, len(scenes)),
                "crossfade": cfg.scene_transition_type not in {"", "fade"},
            },
        )
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "timeline", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "timeline", StepStatus.FAILED, message=str(exc))

    if timeline_path is None:
        update_step_status(project_id, "timeline", StepStatus.FAILED, error="No media available for timeline generation")
        return StepResult(project_id, "timeline", StepStatus.FAILED, message="No media available for timeline generation")

    update_step_status(project_id, "timeline", StepStatus.COMPLETED)
    return StepResult(project_id, "timeline", StepStatus.COMPLETED, outputs={"timeline_path": str(timeline_path)})


def run_render_video(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    # Always reload ffmpeg_render so on-disk fixes are used without server restart
    try:
        import importlib as _il, sys as _sys
        if "src.video.ffmpeg_render" in _sys.modules:
            _il.reload(_sys.modules["src.video.ffmpeg_render"])
    except Exception:
        pass

    ensure_project_files(project_id)
    project_state, cfg = _load_options(project_id, options)
    workflow_state = load_workflow_state(project_id)
    session_settings = {
        "aspect_ratio": cfg.aspect_ratio,
        "enable_subtitles": cfg.include_subtitles,
        "enable_video_effects": cfg.enable_video_effects,
        "video_effects_style": cfg.video_effects_style,
        "enable_music": cfg.include_music,
        "selected_music_track": cfg.selected_music_track,
    }
    resolved_settings = resolve_automation_render_settings(project_id, workflow_state, project_state, session_settings)

    logger = _workflow_logger(project_id)

    timeline_result = run_sync_timeline(project_id, cfg)
    if timeline_result.status != StepStatus.COMPLETED:
        return StepResult(project_id, "render", StepStatus.FAILED, message=timeline_result.message)

    timeline_path = Path(str(timeline_result.outputs.get("timeline_path", "")))
    if not timeline_path.exists():
        return StepResult(project_id, "render", StepStatus.FAILED, message="timeline.json is missing")

    update_step_status(project_id, "render", StepStatus.IN_PROGRESS)

    music_resolution = resolve_music_track_for_project(project_id, resolved_settings.music_track) if resolved_settings.music_enabled else {
        "selected_track": "",
        "resolved_path": "",
        "copied_to_project": False,
        "file_exists": False,
    }
    resolved_music_track = str(music_resolution.get("resolved_path", "") or "")

    expected_settings = {
        "aspect_ratio": resolved_settings.aspect_ratio,
        "subtitles_enabled": resolved_settings.subtitles_enabled,
        "effects_style": resolved_settings.effects_style,
        "music_enabled": resolved_settings.music_enabled,
        "music_track": resolved_music_track,
        "voiceover_enabled": cfg.include_voiceover,
    }
    preflight = preflight_report(project_id, expected_settings=expected_settings)
    preflight["timeline_rebuild_attempted"] = False
    preflight["timeline_rebuild_succeeded"] = False
    preflight["render_preflight_retry"] = False
    logger.info("render_preflight_invalid_timeline=%s", bool(preflight["issues"]["invalid_timeline_references"]))
    if preflight["issues"]["invalid_timeline_references"]:
        logger.warning("auto_rebuilding_timeline_from_disk=True")
        for invalid_reference in preflight["issues"]["invalid_timeline_references"]:
            logger.warning("timeline_reference_invalid path=%s", invalid_reference)
        if int(preflight.get("timeline_scene_count_expected", 0) or 0) != int(preflight.get("timeline_scene_count_actual", 0) or 0):
            logger.warning(
                "timeline_scene_count_mismatch expected=%s actual=%s",
                preflight.get("timeline_scene_count_expected", 0),
                preflight.get("timeline_scene_count_actual", 0),
            )
        preflight["timeline_rebuild_attempted"] = True
        try:
            _invalidate_render_derivatives(project_id)
            timeline_result = _rebuild_timeline_from_disk(project_id, cfg, resolved_settings)
            preflight["timeline_rebuild_succeeded"] = timeline_result.status == StepStatus.COMPLETED
            preflight["render_preflight_retry"] = True
            if timeline_result.status != StepStatus.COMPLETED:
                msg = timeline_result.message or "Timeline auto-rebuild failed before render."
                update_step_status(project_id, "render", StepStatus.FAILED, error=msg)
                return StepResult(project_id, "render", StepStatus.FAILED, message=msg, outputs={"preflight": preflight})
            timeline_path = Path(str(timeline_result.outputs.get("timeline_path", "")))
            if not timeline_path.exists():
                raise RuntimeError("Timeline rebuild reported success but timeline.json is missing.")
            # Reload rebuilt timeline from disk and rerun preflight against fresh state.
            _ = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
            preflight_retry = preflight_report(project_id, expected_settings=expected_settings)
            preflight = preflight_retry | {
                "timeline_rebuild_attempted": True,
                "timeline_rebuild_succeeded": True,
                "render_preflight_retry": True,
            }
            logger.info("timeline_rebuild_attempted=True timeline_rebuild_succeeded=True render_preflight_retry=True")
        except Exception as exc:  # noqa: BLE001
            preflight["timeline_rebuild_succeeded"] = False
            msg = f"Render preflight failed and auto-rebuild errored: {exc}"
            update_step_status(project_id, "render", StepStatus.FAILED, error=msg)
            return StepResult(project_id, "render", StepStatus.FAILED, message=msg, outputs={"preflight": preflight})

    if preflight["issues"]["invalid_timeline_references"]:
        msg = (
            "Render preflight failed after timeline auto-rebuild. "
            "See invalid_timeline_references and scene-count details in preflight report."
        )
        update_step_status(project_id, "render", StepStatus.FAILED, error=msg)
        return StepResult(project_id, "render", StepStatus.FAILED, message=msg, outputs={"preflight": preflight})

    try:
        timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "render", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "render", StepStatus.FAILED, message=str(exc))

    requested_aspect_ratio = resolved_settings.aspect_ratio
    requested_resolution = resolved_settings.output_size
    logger.info(
        "automation_resolved_settings aspect_ratio=%s output_size=%sx%s subtitles_enabled=%s effects_style=%s music_enabled=%s music_track=%s",
        resolved_settings.aspect_ratio,
        resolved_settings.output_width,
        resolved_settings.output_height,
        resolved_settings.subtitles_enabled,
        resolved_settings.effects_style,
        resolved_settings.music_enabled,
        resolved_settings.music_track,
    )
    logger.info(
        "music_track_selected=%s music_track_resolved=%s music_track_copied_to_project=%s music_track_exists=%s",
        music_resolution.get("selected_track", ""),
        resolved_music_track,
        bool(music_resolution.get("copied_to_project", False)),
        bool(music_resolution.get("file_exists", False)),
    )
    timeline_aspect_ratio = normalize_aspect_ratio(timeline.meta.aspect_ratio, requested_aspect_ratio)
    timeline_resolution = str(timeline.meta.resolution or "")
    if timeline_aspect_ratio != requested_aspect_ratio or timeline_resolution != requested_resolution:
        logger.info(
            "timeline_settings_mismatch detected=true requested_aspect_ratio=%s requested_resolution=%s timeline_aspect_ratio=%s timeline_resolution=%s",
            requested_aspect_ratio,
            requested_resolution,
            timeline_aspect_ratio,
            timeline_resolution,
        )
        retry_sync = run_sync_timeline(project_id, cfg)
        if retry_sync.status != StepStatus.COMPLETED:
            msg = f"Aspect-ratio mismatch: requested {requested_aspect_ratio} but timeline has {timeline.meta.aspect_ratio}."
            update_step_status(project_id, "render", StepStatus.FAILED, error=msg)
            return StepResult(project_id, "render", StepStatus.FAILED, message=msg)
        timeline_path = Path(str(retry_sync.outputs.get("timeline_path", "")))
        timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))

    if timeline.meta.include_voiceover and not cfg.allow_silent_render:
        voice_path = timeline.meta.voiceover.path if timeline.meta.voiceover else ""
        if not voice_path or not Path(voice_path).exists():
            msg = "Voiceover is missing and silent render is disabled."
            update_step_status(project_id, "render", StepStatus.FAILED, error=msg)
            return StepResult(project_id, "render", StepStatus.FAILED, message=msg)

    if cfg.allow_silent_render and timeline.meta.include_voiceover:
        voice_path = timeline.meta.voiceover.path if timeline.meta.voiceover else ""
        if not voice_path or not Path(voice_path).exists():
            timeline.meta.include_voiceover = False
            timeline.meta.voiceover = None
            timeline_path = project_dir(project_id) / "renders" / "timeline.silent.json"
            timeline_path.parent.mkdir(parents=True, exist_ok=True)
            timeline_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")

    if resolved_settings.music_enabled and not resolved_music_track:
        msg = "Background music is enabled but no track is selected."
        update_step_status(project_id, "render", StepStatus.FAILED, error=msg)
        return StepResult(project_id, "render", StepStatus.FAILED, message=msg)

    timeline.meta.aspect_ratio = requested_aspect_ratio
    timeline.meta.resolution = requested_resolution
    timeline.meta.burn_captions = should_apply_subtitles(resolved_settings, timeline.meta)
    timeline.meta.include_music = bool(resolved_settings.music_enabled)
    timeline.meta.video_effects_style = resolved_settings.effects_style
    if timeline.meta.include_music:
        # Base attenuation ensures music stays well below a loudnorm'd voiceover
        # (-16 LUFS) even when the slider is at maximum.  The slider ratio maps
        # linearly in dB from this base: ratio=1.0 → -12 dB, ratio=0.25 → -24 dB.
        _MUSIC_BASE_ATTENUATION_DB = -12.0
        music_volume_db = _MUSIC_BASE_ATTENUATION_DB
        try:
            import math
            ratio = max(0.0, min(1.0, float(cfg.music_volume_relative_to_voiceover)))
            if ratio > 0:
                music_volume_db = round(20.0 * math.log10(ratio) + _MUSIC_BASE_ATTENUATION_DB, 2)
            else:
                music_volume_db = -96.0  # effectively muted
        except Exception:
            music_volume_db = _MUSIC_BASE_ATTENUATION_DB
        if not timeline.meta.music:
            from src.video.timeline_schema import Music, Ducking
            timeline.meta.music = Music(path=resolved_music_track, volume_db=music_volume_db, ducking=Ducking(enabled=True))
        timeline.meta.music.path = resolved_music_track
        timeline.meta.music.volume_db = music_volume_db
        if timeline.meta.music.ducking is None:
            from src.video.timeline_schema import Ducking
            timeline.meta.music.ducking = Ducking(enabled=True)
        else:
            timeline.meta.music.ducking.enabled = True
    else:
        timeline.meta.music = None

    timeline_path = project_dir(project_id) / "renders" / "timeline.resolved.json"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")

    warnings: list[str] = []
    music_mix_applied = False
    if timeline.meta.include_music and timeline.meta.music and timeline.meta.music.path:
        if not Path(timeline.meta.music.path).exists():
            msg = f"Music file missing: {timeline.meta.music.path}"
            update_step_status(project_id, "render", StepStatus.FAILED, error=msg)
            return StepResult(project_id, "render", StepStatus.FAILED, message=msg)
        music_mix_applied = True
    if timeline.meta.burn_captions:
        try:
            from src.video.captions import write_ass_file
            _ = write_ass_file
        except Exception:
            timeline.meta.burn_captions = False
            warnings.append("Caption pipeline unavailable; continuing without burned captions.")

    output_path = project_dir(project_id) / "renders" / "final.mp4"
    log_path = project_dir(project_id) / "renders" / "render_logs" / "ffmpeg_last.log"
    report_path = project_dir(project_id) / "renders" / "render_report.json"
    try:
        ensure_ffmpeg_exists()
        if cfg.force_render_rebuild:
            _invalidate_render_derivatives(project_id, include_render_artifacts=True)
        render_video_from_timeline(
            timeline_path,
            output_path,
            log_path=log_path,
            report_path=report_path,
            max_width=2000,
            render_warnings=warnings,
            force_render_rebuild=cfg.force_render_rebuild,
        )
        try:
            _sb_store.upload_video(project_id, output_path.name, output_path)
        except Exception:
            pass
    except (FFmpegNotFoundError, Exception) as exc:  # noqa: BLE001
        if cfg.allow_captionless_render and timeline.meta.burn_captions:
            try:
                timeline.meta.burn_captions = False
                fallback_timeline_path = project_dir(project_id) / "renders" / "timeline.no_captions.json"
                fallback_timeline_path.parent.mkdir(parents=True, exist_ok=True)
                fallback_timeline_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
                render_video_from_timeline(
                    fallback_timeline_path,
                    output_path,
                    log_path=log_path,
                    report_path=report_path,
                    max_width=2000,
                    render_warnings=warnings,
                    force_render_rebuild=cfg.force_render_rebuild,
                )
                warnings.append(f"Caption render failed ({exc}); continued without burned captions.")
            except Exception as retry_exc:  # noqa: BLE001
                update_step_status(project_id, "render", StepStatus.FAILED, error=str(retry_exc))
                return StepResult(project_id, "render", StepStatus.FAILED, message=str(retry_exc), outputs={"warnings": warnings, "preflight": preflight})
        else:
            update_step_status(project_id, "render", StepStatus.FAILED, error=str(exc))
            return StepResult(project_id, "render", StepStatus.FAILED, message=str(exc), outputs={"warnings": warnings, "preflight": preflight})

    if report_path.exists():
        try:
            report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            report_payload["music_enabled"] = bool(timeline.meta.include_music)
            report_payload["music_track"] = str(timeline.meta.music.path if timeline.meta.music and timeline.meta.music.path else "")
            report_payload["music_mix_applied"] = bool(music_mix_applied)
            report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    logger.info(
        "music_enabled=%s music_track=%s music_mix_applied=%s",
        bool(timeline.meta.include_music),
        str(timeline.meta.music.path if timeline.meta.music and timeline.meta.music.path else ""),
        bool(music_mix_applied),
    )
    update_step_status(project_id, "render", StepStatus.COMPLETED)
    return StepResult(project_id, "render", StepStatus.COMPLETED, outputs={"video_path": str(output_path), "warnings": warnings, "preflight": preflight})


def _invalidate_render_derivatives(project_id: str, include_render_artifacts: bool = False) -> None:
    pdir = project_dir(project_id)
    renders_dir = pdir / "renders"
    for candidate in [pdir / "timeline.json", renders_dir / "final.mp4"]:
        if candidate.exists():
            candidate.unlink()
    if include_render_artifacts:
        for candidate in [
            renders_dir / "stitched.mp4",
            renders_dir / "final_tmp.mp4",
            renders_dir / "final_render_logs" / "scene_manifest.json",
        ]:
            if candidate.exists():
                candidate.unlink()
    for timeline_artifact in renders_dir.glob("timeline*.json"):
        if timeline_artifact.exists():
            timeline_artifact.unlink()


def _rebuild_timeline_from_disk(project_id: str, cfg: PipelineOptions, resolved_settings: Any) -> StepResult:
    cfg_for_rebuild = PipelineOptions(**asdict(cfg))
    cfg_for_rebuild.aspect_ratio = resolved_settings.aspect_ratio
    cfg_for_rebuild.include_subtitles = bool(resolved_settings.subtitles_enabled)
    cfg_for_rebuild.enable_video_effects = normalize_video_effects_style(resolved_settings.effects_style) != "Off"
    cfg_for_rebuild.video_effects_style = resolved_settings.effects_style
    cfg_for_rebuild.include_music = bool(resolved_settings.music_enabled)
    cfg_for_rebuild.selected_music_track = str(resolved_settings.music_track or "")
    return run_sync_timeline(project_id, cfg_for_rebuild)
