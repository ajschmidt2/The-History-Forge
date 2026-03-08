"""Reusable workflow step services callable from UI and automation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.storage import record_asset
import src.supabase_storage as _sb_store
from src.ui.timeline_sync import sync_timeline_for_project
from src.video.ffmpeg_render import render_video_from_timeline
from src.video.timeline_builder import compute_scene_durations
from src.video.timeline_schema import Timeline
from src.video.utils import FFmpegNotFoundError, ensure_ffmpeg_exists
from src.workflow.models import StepStatus
from src.workflow.project_io import (
    ensure_project_files,
    load_project_payload,
    load_scenes,
    project_dir,
    save_project_payload,
    save_scenes,
)
from src.workflow.state import load_workflow_state, update_step_status
from utils import (
    generate_image_for_scene,
    generate_prompts_for_scenes,
    generate_script,
    generate_script_from_outline,
    generate_voiceover,
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
    use_ai_video_selected_only: bool = False
    visual_style: str = "Photorealistic cinematic"
    reading_level: str = "General"
    pacing: str = "Balanced"
    allow_silent_render: bool = False
    voice_id: str = ""


@dataclass(slots=True)
class StepResult:
    project_id: str
    step: str
    status: StepStatus
    message: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)


def _load_options(project_id: str, options: PipelineOptions | None) -> tuple[dict[str, Any], PipelineOptions]:
    payload = load_project_payload(project_id)
    merged = options or PipelineOptions()
    merged.tone = payload.get("tone", merged.tone) or merged.tone
    merged.audience = payload.get("audience", merged.audience) or merged.audience
    merged.number_of_scenes = int(payload.get("max_scenes", merged.number_of_scenes) or merged.number_of_scenes)
    merged.variations_per_scene = int(payload.get("variations_per_scene", merged.variations_per_scene) or merged.variations_per_scene)
    merged.aspect_ratio = payload.get("aspect_ratio", merged.aspect_ratio) or merged.aspect_ratio
    merged.visual_style = payload.get("visual_style", merged.visual_style) or merged.visual_style
    merged.reading_level = payload.get("reading_level", merged.reading_level) or merged.reading_level
    merged.pacing = payload.get("pacing", merged.pacing) or merged.pacing
    merged.include_voiceover = bool(payload.get("include_voiceover", merged.include_voiceover))
    merged.include_music = bool(payload.get("include_music", merged.include_music))
    return payload, merged


def run_generate_script(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    load_workflow_state(project_id)
    payload, cfg = _load_options(project_id, options)
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


def run_split_scenes(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    script_text = str(payload.get("script_text", "") or "").strip()
    if not script_text:
        return StepResult(project_id, "scenes", StepStatus.FAILED, message="Script text is missing.")

    update_step_status(project_id, "scenes", StepStatus.IN_PROGRESS)
    try:
        scenes = split_script_into_scenes(script_text, max_scenes=cfg.number_of_scenes, wpm=int(payload.get("scene_wpm", 160) or 160))
        save_scenes(project_id, scenes)
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "scenes", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "scenes", StepStatus.FAILED, message=str(exc))

    update_step_status(project_id, "scenes", StepStatus.COMPLETED)
    return StepResult(project_id, "scenes", StepStatus.COMPLETED, outputs={"scene_count": len(scenes)})


def run_generate_prompts(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    scenes = load_scenes(project_id)
    if not scenes:
        return StepResult(project_id, "prompts", StepStatus.FAILED, message="No scenes available.")

    update_step_status(project_id, "prompts", StepStatus.IN_PROGRESS)
    try:
        scenes = generate_prompts_for_scenes(
            scenes,
            tone=cfg.tone,
            style=cfg.visual_style,
            characters=payload.get("character_registry", []),
            objects=payload.get("object_registry", []),
        )
        save_scenes(project_id, scenes)
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
    try:
        for scene in scenes[: cfg.number_of_scenes]:
            updated = generate_image_for_scene(scene, aspect_ratio=cfg.aspect_ratio, visual_style=cfg.visual_style)
            if updated.image_bytes:
                out = images_dir / f"s{updated.index:02d}.png"
                out.write_bytes(updated.image_bytes)
                record_asset(project_id, "image", out)
                try:
                    _sb_store.upload_image(project_id, out.name, out)
                except Exception:
                    pass
                generated += 1
        save_scenes(project_id, scenes)
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "images", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "images", StepStatus.FAILED, message=str(exc), outputs={"generated": generated})

    update_step_status(project_id, "images", StepStatus.COMPLETED)
    return StepResult(project_id, "images", StepStatus.COMPLETED, outputs={"generated": generated})


def run_generate_voiceover(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    script_text = str(payload.get("script_text", "") or "").strip()
    if not script_text:
        return StepResult(project_id, "voiceover", StepStatus.FAILED, message="Script text is missing.")

    update_step_status(project_id, "voiceover", StepStatus.IN_PROGRESS)
    try:
        audio, err = generate_voiceover(script_text, voice_id=cfg.voice_id or payload.get("voice_id", ""), output_format="mp3")
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "voiceover", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "voiceover", StepStatus.FAILED, message=str(exc))

    if err or not audio:
        message = str(err or "Voiceover generation failed")
        update_step_status(project_id, "voiceover", StepStatus.FAILED, error=message)
        return StepResult(project_id, "voiceover", StepStatus.FAILED, message=message)

    output_path = project_dir(project_id) / "assets/audio/voiceover.mp3"
    output_path.write_bytes(audio)
    record_asset(project_id, "voiceover", output_path)
    try:
        _sb_store.upload_audio(project_id, output_path.name, output_path)
    except Exception:
        pass

    update_step_status(project_id, "voiceover", StepStatus.COMPLETED)
    return StepResult(project_id, "voiceover", StepStatus.COMPLETED, outputs={"voiceover_path": str(output_path)})


def run_sync_timeline(project_id: str, options: PipelineOptions | None = None) -> StepResult:
    ensure_project_files(project_id)
    payload, cfg = _load_options(project_id, options)
    scenes = load_scenes(project_id)
    if not scenes:
        return StepResult(project_id, "timeline", StepStatus.FAILED, message="No scenes available.")

    if any(not (getattr(scene, "image_prompt", "") or "").strip() for scene in scenes):
        run_generate_prompts(project_id, cfg)
        scenes = load_scenes(project_id)

    if any(float(getattr(scene, "estimated_duration_sec", 0.0) or 0.0) <= 0 for scene in scenes):
        excerpts = [str(getattr(scene, "script_excerpt", "") or "") for scene in scenes]
        durations = compute_scene_durations(excerpts, wpm=float(payload.get("scene_wpm", 160) or 160))
        for scene, duration in zip(scenes, durations):
            scene.estimated_duration_sec = float(duration)
        save_scenes(project_id, scenes)

    update_step_status(project_id, "timeline", StepStatus.IN_PROGRESS)
    project_path = project_dir(project_id)
    try:
        timeline_path = sync_timeline_for_project(
            project_path=project_path,
            project_id=project_id,
            title=str(payload.get("project_title", project_id) or project_id),
            session_scenes=scenes,
            meta_overrides={
                "aspect_ratio": cfg.aspect_ratio,
                "include_voiceover": cfg.include_voiceover,
                "include_music": cfg.include_music,
                "transition_types": payload.get("scene_transition_types", []),
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
    ensure_project_files(project_id)
    _, cfg = _load_options(project_id, options)

    timeline_result = run_sync_timeline(project_id, cfg)
    if timeline_result.status != StepStatus.COMPLETED:
        return StepResult(project_id, "render", StepStatus.FAILED, message=timeline_result.message)

    timeline_path = Path(str(timeline_result.outputs.get("timeline_path", "")))
    if not timeline_path.exists():
        return StepResult(project_id, "render", StepStatus.FAILED, message="timeline.json is missing")

    update_step_status(project_id, "render", StepStatus.IN_PROGRESS)
    try:
        timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        update_step_status(project_id, "render", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "render", StepStatus.FAILED, message=str(exc))

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

    output_path = project_dir(project_id) / "renders" / "final.mp4"
    log_path = project_dir(project_id) / "renders" / "render_logs" / "ffmpeg_last.log"
    report_path = project_dir(project_id) / "renders" / "render_report.json"
    try:
        ensure_ffmpeg_exists()
        render_video_from_timeline(timeline_path, output_path, log_path=log_path, report_path=report_path, max_width=1280)
        try:
            _sb_store.upload_video(project_id, output_path.name, output_path)
        except Exception:
            pass
    except (FFmpegNotFoundError, Exception) as exc:  # noqa: BLE001
        update_step_status(project_id, "render", StepStatus.FAILED, error=str(exc))
        return StepResult(project_id, "render", StepStatus.FAILED, message=str(exc))

    update_step_status(project_id, "render", StepStatus.COMPLETED)
    return StepResult(project_id, "render", StepStatus.COMPLETED, outputs={"video_path": str(output_path)})
