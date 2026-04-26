from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.workflow.project_io import load_project_payload, project_dir, save_project_payload
from src.workflow.services import FullWorkflowOptions, PipelineOptions, run_full_workflow


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    project_id = f"codex_short_full_{timestamp}"
    topic = "The Byzantine chain across the Golden Horn that blocked an empire"
    music_track = Path("data/music library/History Track 60 Seconds - String instruments - 1.mp3")
    if not music_track.exists():
        raise FileNotFoundError(f"Music track not found: {music_track}")

    payload = load_project_payload(project_id)
    payload.update(
        {
            "project_id": project_id,
            "project_title": "The Chain Across the Golden Horn",
            "topic": topic,
            "topic_direction": "Tell it like a tense historical turning point with a strong hook and clear stakes.",
            "script_profile": "youtube_short_60s",
            "automation_mode": "topic_to_short_video",
            "aspect_ratio": "9:16",
            "scene_count": 14,
            "max_scenes": 14,
            "visual_style": "Dramatic illustration",
            "enable_music": True,
            "include_music": True,
            "selected_music_track": str(music_track),
            "music_volume_relative_to_voiceover": 0.15,
            "enable_subtitles": False,
            "automation_include_captions": False,
            "automation_generate_voiceover": True,
            "enable_video_effects": True,
            "video_effects_style": "Ken Burns - Standard",
            "tts_provider": "openai",
            "openai_tts_model": "gpt-4o-mini-tts",
            "openai_tts_voice": "ash",
            "ai_video_provider": "google_veo_lite",
            "image_provider": "gemini",
        }
    )
    save_project_payload(project_id, payload)

    pipeline = PipelineOptions(
        number_of_scenes=14,
        aspect_ratio="9:16",
        include_voiceover=True,
        include_music=True,
        visual_style="Dramatic illustration",
        include_subtitles=False,
        enable_video_effects=True,
        video_effects_style="Ken Burns - Standard",
        selected_music_track=str(music_track),
        music_volume_relative_to_voiceover=0.15,
        tts_provider="openai",
        openai_tts_model="gpt-4o-mini-tts",
        openai_tts_voice="ash",
        automation_mode="topic_to_short_video",
        topic=topic,
        topic_direction="Tell it like a tense historical turning point with a strong hook and clear stakes.",
        script_profile="youtube_short_60s",
        ai_video_provider="google_veo_lite",
        image_provider="gemini",
        force_render_rebuild=True,
    )

    progress_path = project_dir(project_id) / "codex_progress.jsonl"

    def progress(event: dict[str, object]) -> None:
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        with progress_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str) + "\n")
        print(json.dumps(event, default=str), flush=True)

    result = run_full_workflow(
        project_id,
        FullWorkflowOptions(
            mode="full_auto",
            overwrite_script=True,
            overwrite_scenes=True,
            overwrite_prompts=True,
            overwrite_images=True,
            overwrite_voiceover=True,
            overwrite_ai_video=True,
            overwrite_timeline=True,
            overwrite_render=True,
            enable_ai_video=True,
            ai_video_provider="google_veo_lite",
            ai_video_seconds=5,
            pipeline=pipeline,
            progress_callback=progress,
        ),
    )

    result_payload = {
        "project_id": result.project_id,
        "completed_steps": result.completed_steps,
        "skipped_steps": result.skipped_steps,
        "failed_step": result.failed_step,
        "final_output_path": result.final_output_path,
        "warnings": result.warnings,
    }
    result_path = project_dir(project_id) / "codex_workflow_result.json"
    result_path.write_text(json.dumps(result_payload, indent=2), encoding="utf-8")
    print("RESULT " + json.dumps(result_payload), flush=True)
    return 0 if not result.failed_step else 1


if __name__ == "__main__":
    raise SystemExit(main())
