"""Project disk I/O helpers used by workflow services."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from src.workflow.state import get_project_manifest


def _load_scene_class() -> type:
    try:
        module = importlib.import_module("utils")
    except Exception:
        module_name = "history_forge_utils_workflow"
        cached = sys.modules.get(module_name)
        if cached is None:
            module_path = Path(__file__).resolve().parents[2] / "utils.py"
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Unable to load utils module from {module_path}")
            cached = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = cached
            spec.loader.exec_module(cached)
        module = cached
    return module.Scene


Scene = _load_scene_class()
PROJECTS_ROOT = Path("data/projects")
PROJECT_STATE_FILENAME = "project_state.json"
SCENES_FILENAME = "scenes.json"


def project_dir(project_id: str) -> Path:
    return PROJECTS_ROOT / str(project_id or "").strip()


def project_state_path(project_id: str) -> Path:
    return project_dir(project_id) / PROJECT_STATE_FILENAME


def scenes_path(project_id: str) -> Path:
    return project_dir(project_id) / SCENES_FILENAME


def _scene_to_dict(scene: Any) -> dict[str, Any]:
    return {
        "index": int(getattr(scene, "index", 0) or 0),
        "title": str(getattr(scene, "title", "") or ""),
        "script_excerpt": str(getattr(scene, "script_excerpt", "") or ""),
        "visual_intent": str(getattr(scene, "visual_intent", "") or ""),
        "scene_id": str(getattr(scene, "scene_id", "") or ""),
        "image_prompt": str(getattr(scene, "image_prompt", "") or ""),
        "status": str(getattr(scene, "status", "active") or "active"),
        "estimated_duration_sec": float(getattr(scene, "estimated_duration_sec", 0.0) or 0.0),
        "video_path": str(getattr(scene, "video_path", "") or ""),
        "video_url": str(getattr(scene, "video_url", "") or ""),
        "video_object_path": str(getattr(scene, "video_object_path", "") or ""),
        "video_loop": bool(getattr(scene, "video_loop", False)),
        "video_muted": bool(getattr(scene, "video_muted", True)),
        "video_volume": float(getattr(scene, "video_volume", 0.0) or 0.0),
        "active_media_type": str(getattr(scene, "active_media_type", "") or ""),
        "asset_paths": dict(getattr(scene, "asset_paths", {}) or {}),
        "asset_urls": dict(getattr(scene, "asset_urls", {}) or {}),
    }


def _scene_from_dict(raw: object) -> Any | None:
    if not isinstance(raw, dict):
        return None
    try:
        idx = int(raw.get("index", 0))
    except (TypeError, ValueError):
        return None
    if idx <= 0:
        return None
    scene = Scene(
        index=idx,
        title=str(raw.get("title", "") or ""),
        script_excerpt=str(raw.get("script_excerpt", "") or ""),
        visual_intent=str(raw.get("visual_intent", "") or ""),
        scene_id=str(raw.get("scene_id", "") or ""),
        image_prompt=str(raw.get("image_prompt", "") or ""),
    )
    scene.status = str(raw.get("status", "active") or "active")
    try:
        scene.estimated_duration_sec = float(raw.get("estimated_duration_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        scene.estimated_duration_sec = 0.0
    scene.video_path = str(raw.get("video_path", "") or "") or None
    scene.video_url = str(raw.get("video_url", "") or "") or None
    scene.video_object_path = str(raw.get("video_object_path", "") or "") or None
    scene.video_loop = bool(raw.get("video_loop", False))
    scene.video_muted = bool(raw.get("video_muted", True))
    try:
        scene.video_volume = float(raw.get("video_volume", 0.0) or 0.0)
    except (TypeError, ValueError):
        scene.video_volume = 0.0
    scene.active_media_type = str(raw.get("active_media_type", "") or "")
    scene.asset_paths = raw.get("asset_paths", {}) if isinstance(raw.get("asset_paths"), dict) else {}
    scene.asset_urls = raw.get("asset_urls", {}) if isinstance(raw.get("asset_urls"), dict) else {}
    return scene


def load_project_payload(project_id: str) -> dict[str, Any]:
    path = project_state_path(project_id)
    if not path.exists():
        return {"project_id": project_id, "scenes": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"project_id": project_id, "scenes": []}
    return raw if isinstance(raw, dict) else {"project_id": project_id, "scenes": []}


def save_project_payload(project_id: str, payload: dict[str, Any]) -> None:
    project = project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    payload["project_id"] = project_id
    project_state_path(project_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_scenes(project_id: str) -> list[Any]:
    path = scenes_path(project_id)
    source: object = None
    if path.exists():
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            source = None
    if not isinstance(source, list):
        source = load_project_payload(project_id).get("scenes", [])
    scenes: list[Any] = []
    if isinstance(source, list):
        for item in source:
            scene = _scene_from_dict(item)
            if scene is not None:
                scenes.append(scene)
    return scenes


def save_scenes(project_id: str, scenes: list[Any]) -> None:
    serial = [_scene_to_dict(scene) for scene in scenes]
    project = project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    scenes_path(project_id).write_text(json.dumps(serial, indent=2), encoding="utf-8")

    payload = load_project_payload(project_id)
    payload["scenes"] = serial
    save_project_payload(project_id, payload)


def ensure_project_files(project_id: str) -> None:
    project = project_dir(project_id)
    (project / "assets/images").mkdir(parents=True, exist_ok=True)
    (project / "assets/videos").mkdir(parents=True, exist_ok=True)
    (project / "assets/audio").mkdir(parents=True, exist_ok=True)
    (project / "assets/music").mkdir(parents=True, exist_ok=True)
    get_project_manifest(project_id)
