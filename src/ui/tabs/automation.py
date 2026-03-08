from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

import streamlit as st

from src.workflow import PIPELINE_STEPS, reset_downstream_steps
from src.workflow.models import StepStatus
from src.workflow.assets import preflight_report, rebuild_timeline_from_disk, regenerate_missing_scene_assets
from src.workflow.project_io import load_project_payload, load_scenes, project_dir, save_project_payload
from src.workflow.services import (
    FullWorkflowOptions,
    PipelineOptions,
    run_full_workflow,
    run_render_video,
    run_sync_timeline,
)
from src.workflow.state import load_workflow_state, save_workflow_state


def _tail_file(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return "".join(deque(handle, maxlen=lines))


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _count_files(folder: Path, suffixes: tuple[str, ...]) -> int:
    if not folder.exists():
        return 0
    valid = {s.lower() for s in suffixes}
    return sum(1 for path in folder.glob("*") if path.is_file() and path.suffix.lower() in valid)


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

    c1, c2, c3 = st.columns(3)
    c1.metric("Overall Status", str(state.overall_status).replace("_", " ").title())
    c2.metric("Current Stage", state.current_stage.replace("_", " ").title())
    c3.metric("Final Render", "Ready" if final_render.exists() else "Missing")

    if state.last_error:
        st.error(f"Last error: {state.last_error}")

    st.markdown("#### Step Status")
    cols = st.columns(3)
    for idx, step in enumerate(PIPELINE_STEPS):
        status = state.step_statuses.get(step, StepStatus.NOT_STARTED)
        icon = {
            StepStatus.COMPLETED: "✅",
            StepStatus.FAILED: "❌",
            StepStatus.IN_PROGRESS: "⏳",
            StepStatus.SKIPPED: "⏭️",
            StepStatus.NEEDS_REVIEW: "⚠️",
            StepStatus.NOT_STARTED: "▫️",
        }.get(status, "▫️")
        cols[idx % 3].write(f"{icon} **{step.replace('_', ' ').title()}** — {status.value}")

    st.markdown("#### Asset Dashboard")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Scenes", counts["scenes"])
    a2.metric("Prompts", counts["prompts"])
    a3.metric("Images", counts["images"])
    a4.metric("Voiceover Files", counts["voiceover"])
    b1, b2, b3 = st.columns(3)
    b1.metric("Music Files", counts["music"])
    b2.metric("Video Files", counts["videos"])
    b3.metric("Scenes With AI Video", counts["ai_video_scenes"])

    checklist = {
        "Script ready": bool(script_text),
        "Scenes ready": counts["scenes"] > 0,
        "Prompts ready": counts["prompts"] >= counts["scenes"] > 0,
        "Visuals ready": counts["images"] + counts["videos"] >= max(1, counts["scenes"]),
        "Voiceover ready": counts["voiceover"] > 0,
        "Timeline ready": timeline_path.exists(),
        "Render ready": final_render.exists(),
    }
    st.markdown("#### Pipeline Checklist")
    for label, ready in checklist.items():
        st.write(f"{'✅' if ready else '⬜'} {label}")

    missing: list[str] = []
    if counts["scenes"] and counts["images"] + counts["videos"] < counts["scenes"]:
        missing.append("Some scenes do not have generated visual assets yet.")
    if checklist["Timeline ready"] and not checklist["Voiceover ready"]:
        missing.append("Timeline exists but no voiceover file found.")
    if missing:
        st.warning("\n".join(f"• {item}" for item in missing))


    st.markdown("#### Render Preflight")
    preflight = preflight_report(project_id)
    if preflight["ok"]:
        st.success("Preflight passed. Timeline/media references look healthy.")
    else:
        st.warning(f"Preflight found {preflight['issue_count']} issue(s).")
        st.json(preflight)

    image_based = sum(1 for scene in scenes if not (getattr(scene, "video_path", "") or getattr(scene, "video_url", "")))
    video_based = len(scenes) - image_based
    st.caption(f"Scene media mix: {image_based} image-based, {video_based} video-based.")

    enabled_ai_video = bool(payload.get("automation_enable_ai_video", False))
    if enabled_ai_video and image_based > 0:
        st.info("AI video is enabled but some scenes are still image-based. Fallback: render now with images, then regenerate selected scenes with AI video and rerender.")

    st.markdown("#### Automation Settings")
    col_settings_1, col_settings_2 = st.columns(2)
    with col_settings_1:
        use_web_research = st.toggle("Use web research", value=bool(payload.get("use_web_research", False)))
        generate_images = st.toggle("Generate images", value=bool(payload.get("automation_generate_images", True)))
        generate_voiceover = st.toggle("Generate voiceover", value=bool(payload.get("automation_generate_voiceover", True)))
        generate_ai_video = st.toggle("Generate AI video for selected scenes", value=bool(payload.get("automation_enable_ai_video", False)))
    with col_settings_2:
        include_music = st.toggle("Include music", value=bool(payload.get("include_music", False)))
        include_captions = st.toggle("Include captions", value=bool(payload.get("automation_include_captions", True)))
        overwrite_existing = st.toggle("Overwrite existing assets", value=bool(payload.get("automation_overwrite_existing", False)))
        include_voiceover = st.toggle("Include voiceover in timeline/render", value=bool(payload.get("include_voiceover", True)))

    if st.button("Save automation settings", width="stretch"):
        payload["use_web_research"] = use_web_research
        payload["include_music"] = include_music
        payload["include_voiceover"] = include_voiceover
        payload["automation_generate_images"] = generate_images
        payload["automation_generate_voiceover"] = generate_voiceover
        payload["automation_enable_ai_video"] = generate_ai_video
        payload["automation_include_captions"] = include_captions
        payload["automation_overwrite_existing"] = overwrite_existing
        save_project_payload(project_id, payload)
        st.success("Automation settings saved.")

    pipeline_options = PipelineOptions(
        use_web_research=use_web_research,
        include_music=include_music,
        include_voiceover=include_voiceover,
        voice_id=str(payload.get("voice_id", "") or ""),
        allow_silent_render=not include_voiceover,
    )

    st.markdown("#### Controls")
    c_full, c_resume, c_timeline, c_render = st.columns(4)
    c_assets, c_rebuild = st.columns(2)
    if c_full.button("Run Full Workflow", width="stretch"):
        result = run_full_workflow(
            project_id,
            FullWorkflowOptions(
                mode="full_auto",
                overwrite_script=overwrite_existing,
                overwrite_scenes=overwrite_existing,
                overwrite_prompts=overwrite_existing,
                overwrite_images=overwrite_existing and generate_images,
                overwrite_voiceover=overwrite_existing and generate_voiceover,
                overwrite_ai_video=overwrite_existing and generate_ai_video,
                overwrite_timeline=overwrite_existing,
                overwrite_render=overwrite_existing,
                enable_ai_video=generate_ai_video,
                pipeline=pipeline_options,
            ),
        )
        if result.failed_step:
            st.error(f"Workflow stopped at: {result.failed_step}")
        else:
            st.success("Workflow run completed.")
        if result.final_output_path:
            st.success(f"Final render: {result.final_output_path}")

    if c_resume.button("Resume Missing Steps", width="stretch"):
        result = run_full_workflow(
            project_id,
            FullWorkflowOptions(
                mode="resume_missing",
                enable_ai_video=generate_ai_video,
                pipeline=pipeline_options,
            ),
        )
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

    st.markdown("#### Logs")
    st.caption("Recent workflow log lines")
    st.code(_tail_file(workflow_log, lines=120) or "No workflow.log lines yet.", language="text")

    st.caption("Last render report")
    report_payload = _load_json(render_report)
    if report_payload:
        st.json(report_payload)
    else:
        st.info("No render report found.")

    if final_render.exists():
        st.write(f"Final render path: `{final_render}`")
