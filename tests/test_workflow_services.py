import json
from pathlib import Path

from src.workflow.models import StepStatus
from src.workflow.project_io import load_scenes, save_project_payload
from src.workflow.services import PipelineOptions, run_split_scenes, run_sync_timeline


def test_run_split_scenes_persists_scene_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-scenes"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "script_text": "One short paragraph about history. Another sentence for timing.",
            "max_scenes": 3,
        },
    )

    result = run_split_scenes(project_id, PipelineOptions(number_of_scenes=3))
    assert result.status == StepStatus.COMPLETED

    scenes_file = Path("data/projects") / project_id / "scenes.json"
    assert scenes_file.exists()
    scenes = json.loads(scenes_file.read_text(encoding="utf-8"))
    assert isinstance(scenes, list)
    assert len(scenes) >= 1


def test_run_sync_timeline_fills_missing_durations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "svc-timeline"
    save_project_payload(
        project_id,
        {
            "project_id": project_id,
            "project_title": "Svc Timeline",
            "scene_wpm": 160,
            "scene_transition_types": [],
        },
    )
    scenes = [
        {
            "index": 1,
            "title": "S1",
            "script_excerpt": "A brief narration line.",
            "visual_intent": "v1",
            "image_prompt": "",
            "estimated_duration_sec": 0,
        },
        {
            "index": 2,
            "title": "S2",
            "script_excerpt": "Another brief narration line for timing.",
            "visual_intent": "v2",
            "image_prompt": "",
            "estimated_duration_sec": 0,
        },
    ]
    (Path("data/projects") / project_id).mkdir(parents=True, exist_ok=True)
    (Path("data/projects") / project_id / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")

    def _fake_prompts(pid, options):
        loaded = load_scenes(pid)
        for scene in loaded:
            scene.image_prompt = f"Prompt {scene.index}"
        from src.workflow.project_io import save_scenes

        save_scenes(pid, loaded)
        from src.workflow.services import StepResult

        return StepResult(project_id=pid, step="prompts", status=StepStatus.COMPLETED)

    def _fake_sync(**kwargs):
        timeline_path = kwargs["project_path"] / "timeline.json"
        timeline_path.write_text("{}", encoding="utf-8")
        return timeline_path

    monkeypatch.setattr("src.workflow.services.run_generate_prompts", _fake_prompts)
    monkeypatch.setattr("src.workflow.services.sync_timeline_for_project", _fake_sync)

    result = run_sync_timeline(project_id, PipelineOptions())
    assert result.status == StepStatus.COMPLETED

    refreshed = load_scenes(project_id)
    assert all(float(scene.estimated_duration_sec) > 0 for scene in refreshed)
