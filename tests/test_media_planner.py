from __future__ import annotations

from types import SimpleNamespace

from src.workflow.media_planner import plan_media_for_scenes


def test_plan_media_for_scenes_uses_structured_router_output(monkeypatch):
    scenes = [
        SimpleNamespace(index=1, title="Factory fire", script_excerpt="Newspaper photographers documented the aftermath.", visual_intent="smoke and ruined building"),
        SimpleNamespace(index=2, title="Harbor escape", script_excerpt="Ships cutting through rough water at dawn.", visual_intent="fast-moving sea chase"),
    ]

    class Router:
        def generate_structured(self, prompt, *, system=None, task_type="json"):
            return """{
              "plans": [
                {
                  "scene_index": 1,
                  "primary_asset": "real_image",
                  "real_image_search_terms": ["factory fire newspaper photo", "factory fire archive"],
                  "broll_query": "factory fire smoke crowd",
                  "notes": "archival coverage is likely"
                },
                {
                  "scene_index": 2,
                  "primary_asset": "broll",
                  "real_image_search_terms": ["historic harbor ships"],
                  "broll_query": "stormy harbor ships sunrise",
                  "notes": "motion sells this beat"
                }
              ]
            }"""

    monkeypatch.setattr("src.workflow.media_planner.get_router", lambda: Router())

    plans = plan_media_for_scenes(scenes, topic="Industrial disasters", era="early 1900s", aspect_ratio="9:16")

    assert plans[1]["primary_asset"] == "real_image"
    assert plans[1]["real_image_search_terms"][0] == "factory fire newspaper photo"
    assert plans[2]["primary_asset"] == "broll"
    assert plans[2]["broll_query"] == "stormy harbor ships sunrise"


def test_plan_media_for_scenes_falls_back_to_heuristics(monkeypatch):
    scenes = [
        SimpleNamespace(index=1, title="1940s street scene", script_excerpt="A documentary photo captured the crowd outside the courthouse.", visual_intent="period crowd on a city street"),
        SimpleNamespace(index=2, title="Ancient march", script_excerpt="Roman soldiers march through dust and banners.", visual_intent="advancing column of soldiers"),
    ]

    class BrokenRouter:
        def generate_structured(self, prompt, *, system=None, task_type="json"):
            raise RuntimeError("ollama offline")

    monkeypatch.setattr("src.workflow.media_planner.get_router", lambda: BrokenRouter())

    plans = plan_media_for_scenes(scenes, topic="History", era="", aspect_ratio="9:16")

    assert plans[1]["primary_asset"] == "real_image"
    assert plans[2]["primary_asset"] in {"broll", "ai_image"}
