import json
from pathlib import Path

from src.video.timeline_schema import Meta, Scene as TimelineScene, Timeline
from src.workflow.assets import (
    canonical_scene_image_path,
    canonical_scene_video_path,
    preflight_report,
    regenerate_missing_scene_assets,
    repair_timeline_media_references,
    resolve_music_track_for_project,
    sync_scene_asset_metadata,
)
from src.workflow.project_io import load_scenes, save_scenes
from src.workflow.state import get_project_manifest
from utils import Scene


def test_manifest_load_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "asset-manifest"
    manifest = get_project_manifest(project_id)
    path = Path("data/projects") / project_id / "project_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["images"].endswith("assets/images")
    assert manifest.project_id == project_id


def test_scene_serialization_roundtrip_with_asset_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "scene-roundtrip"
    scene = Scene(index=1, title="One", script_excerpt="Excerpt", visual_intent="Intent", image_prompt="Prompt")
    scene.active_media_type = "image"
    scene.asset_paths = {"image": "data/projects/scene-roundtrip/assets/images/s01.png"}
    scene.asset_urls = {"video_url": ""}
    save_scenes(project_id, [scene])

    loaded = load_scenes(project_id)
    assert len(loaded) == 1
    assert getattr(loaded[0], "active_media_type", "") == "image"
    assert getattr(loaded[0], "asset_paths", {}).get("image", "").endswith("s01.png")


def test_fallback_selection_prefers_video_then_image(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "fallback"
    pdir = Path("data/projects") / project_id
    (pdir / "assets/images").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/videos").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/images/s01.png").write_bytes(b"png")
    (pdir / "assets/videos/scene01.mp4").write_bytes(b"mp4")

    scene = Scene(index=1, title="One", script_excerpt="Excerpt", visual_intent="Intent", image_prompt="Prompt")
    scene.video_path = str(pdir / "assets/videos/scene01.mp4")
    save_scenes(project_id, [scene])

    synced = sync_scene_asset_metadata(project_id)
    assert getattr(synced[0], "active_media_type", "") == "video"
    assert canonical_scene_video_path(project_id, 1).exists()

    canonical_scene_video_path(project_id, 1).unlink()
    synced2 = sync_scene_asset_metadata(project_id)
    assert getattr(synced2[0], "active_media_type", "") == "image"
    assert canonical_scene_image_path(project_id, 1).exists()


def test_regenerate_missing_and_preflight_find_actionable_issues(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "preflight"
    scene = Scene(index=1, title="One", script_excerpt="Excerpt", visual_intent="Intent", image_prompt="Prompt")
    save_scenes(project_id, [scene])

    regen = regenerate_missing_scene_assets(project_id)
    assert regen["missing_images"] == [1]

    timeline = Timeline(
        meta=Meta(project_id=project_id, title="t"),
        scenes=[TimelineScene(id="bad", image_path="/does/not/exist.png", start=0, duration=2)],
    )
    pdir = Path("data/projects") / project_id
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "timeline.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")

    report = preflight_report(project_id)
    assert not report["ok"]
    assert report["issues"]["missing_voiceover"]
    assert report["issues"]["invalid_timeline_references"]
    assert report["actions"]


def test_preflight_reports_scene_count_and_metadata_mismatch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "preflight-mismatch"
    scene = Scene(index=1, title="One", script_excerpt="Excerpt", visual_intent="Intent", image_prompt="Prompt")
    save_scenes(project_id, [scene])

    pdir = Path("data/projects") / project_id
    (pdir / "assets/images").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/images/s01.png").write_bytes(b"png")
    (pdir / "assets/images/s02.png").write_bytes(b"png")

    timeline = Timeline(
        meta=Meta(project_id=project_id, title="t", aspect_ratio="16:9", burn_captions=True, include_music=False),
        scenes=[
            TimelineScene(id="s01", image_path=str(pdir / "assets/images/s01.png"), start=0, duration=2),
            TimelineScene(id="s02", image_path=str(pdir / "assets/images/s02.png"), start=2, duration=2),
        ],
    )
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "timeline.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")

    report = preflight_report(
        project_id,
        expected_settings={
            "aspect_ratio": "9:16",
            "subtitles_enabled": False,
            "music_enabled": True,
            "effects_style": "Ken Burns - Standard",
        },
    )
    assert report["timeline_scene_count_expected"] == 1
    assert report["timeline_scene_count_actual"] == 2
    assert any("timeline_scene_count_mismatch" in item for item in report["issues"]["invalid_timeline_references"])
    assert any("timeline_metadata_mismatch" in item for item in report["issues"]["invalid_timeline_references"])


def test_resolve_music_track_copies_shared_library_track_into_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "music-resolve"
    shared = Path("data/music library")
    shared.mkdir(parents=True, exist_ok=True)
    src_track = shared / "track.mp3"
    src_track.write_bytes(b"mp3")

    resolved = resolve_music_track_for_project(project_id, str(src_track))

    assert resolved["selected_track"] == str(src_track)
    assert resolved["copied_to_project"] is True
    assert resolved["file_exists"] is True
    assert "/assets/music/track.mp3" in resolved["resolved_path"]
    assert Path(resolved["resolved_path"]).exists()


def test_preflight_music_metadata_uses_resolved_track_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "preflight-music"
    scene = Scene(index=1, title="One", script_excerpt="Excerpt", visual_intent="Intent", image_prompt="Prompt")
    save_scenes(project_id, [scene])

    pdir = Path("data/projects") / project_id
    (pdir / "assets/images").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/images/s01.png").write_bytes(b"png")
    shared = Path("data/music_library")
    shared.mkdir(parents=True, exist_ok=True)
    selected_track = shared / "library.mp3"
    selected_track.write_bytes(b"mp3")

    report_for_expected = resolve_music_track_for_project(project_id, str(selected_track))
    canonical = report_for_expected["resolved_path"]

    timeline = Timeline(
        meta=Meta(
            project_id=project_id,
            title="t",
            include_music=True,
            music={"path": canonical, "volume_db": -6, "ducking": {"enabled": False}},
        ),
        scenes=[TimelineScene(id="s01", image_path=str(pdir / "assets/images/s01.png"), start=0, duration=2)],
    )
    (pdir / "timeline.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")

    report = preflight_report(
        project_id,
        expected_settings={"music_enabled": True, "music_track": str(selected_track)},
    )

    assert report["music_track_selected"] == str(selected_track)
    assert report["music_track_resolved"] == canonical
    assert report["music_track_exists"] is True
    assert report.get("timeline_music_attached") is True
    assert not any("timeline_metadata_mismatch music_track" in item for item in report["issues"]["invalid_timeline_references"])


def test_preflight_does_not_report_scene_count_mismatch_when_equal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "preflight-equal-count"
    scene = Scene(index=1, title="One", script_excerpt="Excerpt", visual_intent="Intent", image_prompt="Prompt")
    save_scenes(project_id, [scene])

    pdir = Path("data/projects") / project_id
    (pdir / "assets/images").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/images/s01.png").write_bytes(b"png")

    timeline = Timeline(
        meta=Meta(project_id=project_id, title="t"),
        scenes=[TimelineScene(id="s01", image_path=str(pdir / "assets/images/s01.png"), start=0, duration=2)],
    )
    (pdir / "timeline.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")

    report = preflight_report(project_id)
    assert not any("timeline_scene_count_mismatch" in item for item in report["issues"]["invalid_timeline_references"])


def test_repair_timeline_media_references_restores_canonical_scene_image(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_id = "repair-media-refs"
    scene = Scene(index=1, title="One", script_excerpt="Excerpt", visual_intent="Intent", image_prompt="Prompt")
    save_scenes(project_id, [scene])

    pdir = Path("data/projects") / project_id
    images_dir = pdir / "assets/images"
    images_dir.mkdir(parents=True, exist_ok=True)
    alt = images_dir / "scene01.png"
    alt.write_bytes(b"png")

    timeline = Timeline(
        meta=Meta(project_id=project_id, title="t"),
        scenes=[TimelineScene(id="s01", image_path=str(images_dir / "s01.png"), start=0, duration=2)],
    )

    result = repair_timeline_media_references(project_id, timeline, load_scenes(project_id))

    assert result["changed"] is True
    assert result["repaired_scene_indexes"] == [1]
    assert (images_dir / "s01.png").exists()
    assert timeline.scenes[0].image_path.endswith("assets/images/s01.png")
