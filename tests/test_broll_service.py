from __future__ import annotations

from types import SimpleNamespace

import src.broll.service as service


def test_search_broll_skips_unconfigured_provider(monkeypatch):
    monkeypatch.setattr(service, "broll_provider_status", lambda: {"pexels": False, "pixabay": True})
    monkeypatch.setattr(service, "search_pixabay_videos", lambda *args, **kwargs: [])

    result = service.search_broll("roman empire", "16:9", ["pexels", "pixabay"], 3)

    assert result == []
    assert "Pexels API key not found in Streamlit secrets." in service.get_last_search_errors()


def test_search_broll_returns_first_success(monkeypatch):
    monkeypatch.setattr(service, "broll_provider_status", lambda: {"pexels": True, "pixabay": True})
    monkeypatch.setattr(service, "search_pexels_videos", lambda *args, **kwargs: [])

    class Obj:
        provider = "pixabay"

    monkeypatch.setattr(service, "search_pixabay_videos", lambda *args, **kwargs: [Obj()])

    result = service.search_broll("roman empire", "16:9", ["pexels", "pixabay"], 3)

    assert len(result) == 1
    assert result[0].provider == "pixabay"


def test_search_broll_for_scene_ranks_more_relevant_clip(monkeypatch):
    monkeypatch.setattr(service, "broll_provider_status", lambda: {"pexels": True, "pixabay": True})

    generic = service.BrollResult(
        provider="pexels",
        id="1",
        title="people walking city generic stock footage",
        duration_sec=5.0,
        width=720,
        height=1280,
        orientation="vertical",
        preview_image_url="",
        video_url="https://example.com/generic.mp4",
        page_url="https://example.com/generic",
        attribution_text="Video on Pexels",
        license_note="free",
    )
    relevant = service.BrollResult(
        provider="pixabay",
        id="2",
        title="world war ii resistance bicycle escape occupied france",
        duration_sec=6.0,
        width=720,
        height=1280,
        orientation="vertical",
        preview_image_url="",
        video_url="https://example.com/relevant.mp4",
        page_url="https://example.com/relevant",
        attribution_text="Video on Pixabay",
        license_note="free",
    )

    monkeypatch.setattr(service, "search_pexels_videos", lambda *args, **kwargs: [generic])
    monkeypatch.setattr(service, "search_pixabay_videos", lambda *args, **kwargs: [relevant])

    scene = SimpleNamespace(
        broll_query="world war ii resistance bicycle escape occupied france",
        visual_intent="resistance courier escaping by bicycle",
        script_excerpt="She slipped through occupied France on a bicycle, evading patrols.",
    )

    results = service.search_broll_for_scene(scene, aspect_ratio="9:16", provider_priority=["pexels", "pixabay"], verification_level="strict")

    assert results
    assert results[0].id == "2"


def test_generate_broll_query_prefers_media_plan_hint():
    scene = SimpleNamespace(
        broll_query="",
        prompt_spec={"media_plan": {"broll_query": "stormy atlantic ocean ship deck"}},
        visual_intent="pirates",
        script_excerpt="ship at sea",
    )

    assert service.generate_broll_query_for_scene(scene) == "stormy atlantic ocean ship deck"


def test_auto_assign_broll_to_scenes_uses_broll_preference(monkeypatch, tmp_path):
    project_id = "broll-auto"
    scene = SimpleNamespace(
        index=1,
        active_media_type="broll",
        prompt_spec={"media_plan": {"primary_asset": "broll", "broll_query": "medieval harbor ships"}},
        use_broll=False,
        broll_local_path="",
        video_path="",
        video_object_path="",
        broll_query="",
    )

    result = SimpleNamespace(
        provider="pexels",
        id="abc",
        duration_sec=4.0,
        orientation="vertical",
        video_url="https://example.com/clip.mp4",
        page_url="https://example.com/page",
    )
    downloaded = tmp_path / "clip.mp4"
    downloaded.write_bytes(b"clip")

    monkeypatch.setattr(service, "search_broll_for_scene", lambda *args, **kwargs: [result])
    monkeypatch.setattr(service, "download_broll_asset", lambda *args, **kwargs: downloaded)

    searched, assigned = service.auto_assign_broll_to_scenes(project_id, [scene], aspect_ratio="9:16")

    assert searched == 1
    assert assigned == 1
    assert scene.use_broll is True
    assert scene.broll_local_path == str(downloaded)
