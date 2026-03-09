from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

import streamlit as st

from src.ui.constants import VISUAL_STYLE_OPTIONS
from src.ui.state import DEFAULT_VOICE_ID
from src.workflow import PIPELINE_STEPS, reset_downstream_steps
from src.workflow.assets import canonical_scene_image_path, preflight_report, rebuild_timeline_from_disk, regenerate_missing_scene_assets
from src.workflow.models import StepStatus
from src.workflow.project_io import load_project_payload, load_scenes, project_dir, save_project_payload
from src.workflow.services import (
    FullWorkflowOptions,
    PipelineOptions,
    run_full_workflow,
    run_render_video,
    run_sync_timeline,
)
from src.workflow.state import load_workflow_state, save_workflow_state

MUSIC_LIBRARY_ROOT = Path("data/music_library")
PREFERENCES_PATH = Path("data/user_preferences.json")
AUTOMATION_STEP_ORDER: tuple[str, ...] = ("voiceover", "scenes", "narrative", "prompts", "images", "effects", "render")


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


def _render_workflow_progress(project_id: str, progress_holder: Any, log_holder: Any, error_holder: Any, output_holder: Any) -> None:
    state = load_workflow_state(project_id)
    completed = sum(1 for step in AUTOMATION_STEP_ORDER if state.step_statuses.get(step) in {StepStatus.COMPLETED, StepStatus.SKIPPED})
    ratio = completed / max(1, len(AUTOMATION_STEP_ORDER))
    current = state.current_stage if state.current_stage in AUTOMATION_STEP_ORDER else AUTOMATION_STEP_ORDER[0]
    current_idx = AUTOMATION_STEP_ORDER.index(current) + 1

    with progress_holder.container():
        st.progress(ratio)
        st.write(f"Running step {current_idx} of {len(AUTOMATION_STEP_ORDER)}: {current.replace('_', ' ').title()}")
        cols = st.columns(len(AUTOMATION_STEP_ORDER))
        for idx, step in enumerate(AUTOMATION_STEP_ORDER):
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
        st.image(str(image_path), caption=f"Current image · {image_path.name}", use_container_width=True)
    else:
        st.caption("No image file found for this scene yet.")

    if st.button("Save scene edits", key=f"automation_save_scene_{project_id}_{selected.index}", width="stretch"):
        selected.title = edited_title.strip()
        selected.script_excerpt = edited_excerpt.strip()
        selected.image_prompt = edited_prompt.strip()
        save_scenes(project_id, scenes)
        st.success("Scene updates saved. Re-run prompts/images/render to apply changes to the final video.")


def tab_automation(project_id: str) -> None:
    st.subheader("Automation")
    project_path = project_dir(project_id)
    payload = load_project_payload(project_id)
    state = load_workflow_state(project_id)
    counts = _asset_counts(project_id)

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
    _render_workflow_progress(project_id, progress_holder, log_holder, error_holder, output_holder)

    st.markdown("#### Automation Settings")
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
    with col_settings_1:
        aspect_ratio = st.selectbox("Aspect Ratio", options=["16:9", "9:16"], index=0 if str(payload.get("aspect_ratio", "16:9")) == "16:9" else 1)
        visual_style = st.selectbox("Visual Style", options=list(VISUAL_STYLE_OPTIONS), index=list(VISUAL_STYLE_OPTIONS).index(current_style))
        scene_count = st.number_input("Number of Scenes", min_value=1, max_value=75, value=int(payload.get("scene_count", payload.get("max_scenes", 8)) or 8), step=1)
        enable_video_effects = st.toggle("Video Effects", value=bool(payload.get("enable_video_effects", True)))
    with col_settings_2:
        enable_subtitles = st.toggle("Subtitles", value=bool(payload.get("enable_subtitles", payload.get("automation_include_captions", True))))
        enable_music = st.toggle("Background Music", value=bool(payload.get("enable_music", payload.get("include_music", False))))
        generate_voiceover = st.toggle("Generate voiceover", value=bool(payload.get("automation_generate_voiceover", True)))
        overwrite_existing = st.toggle("Overwrite existing assets", value=bool(payload.get("automation_overwrite_existing", False)))

    combined_music_choices: list[tuple[str, str]] = []
    for track in project_music_tracks:
        combined_music_choices.append((f"Project · {track.name}", str(track)))
    for track in shared_music_tracks:
        combined_music_choices.append((f"Shared · {track.name}", str(track)))

    selected_music_track = str(payload.get("selected_music_track", "") or "")
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

    selected_voice_id = st.selectbox(
        "Voice ID",
        options=known_voice_ids,
        index=known_voice_ids.index(str(payload.get("voice_id", "") or "")) if str(payload.get("voice_id", "") or "") in known_voice_ids else 0,
        help="Automation resolves Voice ID in order: selected value, saved preference, then DEFAULT_VOICE_ID.",
    )
    resolved_voice_id, resolved_source = _resolve_voice_id(selected_voice_id, payload)
    st.caption(f"Resolved Voice ID: `{resolved_voice_id or 'None'}` ({resolved_source})")

    if st.button("Save automation settings", width="stretch"):
        payload.update({
            "aspect_ratio": aspect_ratio,
            "image_style": visual_style,
            "visual_style": visual_style,
            "scene_count": int(scene_count),
            "max_scenes": int(scene_count),
            "enable_video_effects": bool(enable_video_effects),
            "enable_music": bool(enable_music),
            "include_music": bool(enable_music),
            "selected_music_track": selected_music_track if enable_music else "",
            "enable_subtitles": bool(enable_subtitles),
            "automation_generate_voiceover": bool(generate_voiceover),
            "automation_overwrite_existing": bool(overwrite_existing),
            "music_volume_relative_to_voiceover": 0.5,
            "voice_id": selected_voice_id,
        })
        save_project_payload(project_id, payload)
        st.success("Automation settings saved.")

    if generate_voiceover and not resolved_voice_id:
        st.warning("No Voice ID could be resolved. Select a voice before running automation.")
    if enable_music and not selected_music_track:
        st.warning("Background music is enabled, but no music track is selected.")

    pipeline_options = PipelineOptions(
        include_music=enable_music,
        include_subtitles=enable_subtitles,
        include_voiceover=bool(generate_voiceover),
        number_of_scenes=int(scene_count),
        aspect_ratio=aspect_ratio,
        visual_style=visual_style,
        enable_video_effects=enable_video_effects,
        selected_music_track=selected_music_track,
        music_volume_relative_to_voiceover=0.5,
        voice_id=selected_voice_id,
        allow_silent_render=False,
    )

    st.markdown("#### Controls")
    c_full, c_resume, c_timeline, c_render = st.columns(4)
    c_assets, c_rebuild = st.columns(2)

    def _progress_callback(event: dict[str, Any]) -> None:
        _render_workflow_progress(project_id, progress_holder, log_holder, error_holder, output_holder)

    if c_full.button("Run Full Workflow", width="stretch"):
        if enable_music and not selected_music_track:
            st.error("Background music is enabled but no track is selected.")
            return
        if generate_voiceover and not resolved_voice_id:
            st.error("Voice ID is required for voiceover.")
            return
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
        _render_workflow_progress(project_id, progress_holder, log_holder, error_holder, output_holder)
        if result.failed_step:
            st.error(f"Workflow stopped at: {result.failed_step}")
        else:
            st.success("Workflow run completed.")
        if result.final_output_path:
            st.success(f"Final render: {result.final_output_path}")
            st.rerun()

    if c_resume.button("Resume Missing Steps", width="stretch"):
        result = run_full_workflow(project_id, FullWorkflowOptions(mode="resume_missing", pipeline=pipeline_options, progress_callback=_progress_callback))
        _render_workflow_progress(project_id, progress_holder, log_holder, error_holder, output_holder)
        if result.failed_step:
            st.error(f"Resume failed at: {result.failed_step}")
        else:
            st.success("Resume completed.")

    if c_timeline.button("Rebuild Timeline", width="stretch"):
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
        result = run_render_video(project_id, pipeline_options)
        if result.status == StepStatus.COMPLETED:
            st.success("Render completed.")
            st.rerun()
        else:
            st.error(result.message or "Render failed.")

    failed_candidates = [step for step in PIPELINE_STEPS if state.step_statuses.get(step) == StepStatus.FAILED]
    reset_step = st.selectbox("Failed step to reset", options=failed_candidates or ["script"], key=f"automation_reset_{project_id}")
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
    if preflight["ok"]:
        st.success("Preflight passed. Timeline/media references look healthy.")
    else:
        st.warning(f"Preflight found {preflight['issue_count']} issue(s).")

    st.markdown("#### Logs")
    st.caption("Recent workflow log lines")
    st.code(_tail_file(workflow_log, lines=120) or "No workflow.log lines yet.", language="text")

    st.caption("Last render report")
    report_payload = _load_json(render_report)
    if report_payload:
        st.json(report_payload)
    else:
        st.info("No render report found.")

    _render_post_run_video_section(project_id, final_render)
    _render_quick_scene_edits(project_id, scenes)
