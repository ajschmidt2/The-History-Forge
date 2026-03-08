"""Scene asset canon + validation helpers for deterministic automation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.video.timeline_schema import Timeline
from src.workflow.project_io import load_scenes, project_dir, save_scenes

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac"}


def canonical_scene_id(index: int) -> str:
    return f"s{int(index):02d}"


def canonical_scene_image_path(project_id: str, index: int) -> Path:
    return project_dir(project_id) / "assets/images" / f"{canonical_scene_id(index)}.png"


def canonical_scene_video_path(project_id: str, index: int) -> Path:
    return project_dir(project_id) / "assets/videos" / f"{canonical_scene_id(index)}.mp4"


def canonical_scene_effect_path(project_id: str, index: int) -> Path:
    return project_dir(project_id) / "assets/effects" / f"{canonical_scene_id(index)}.mp4"


def canonical_scene_meta_path(project_id: str, index: int) -> Path:
    return project_dir(project_id) / "assets/scene_meta" / f"{canonical_scene_id(index)}.json"


def _normalize_existing_to_canonical(target: Path, alternates: list[Path]) -> Path | None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    for candidate in alternates:
        if not candidate.exists() or candidate.resolve() == target.resolve() or candidate.stat().st_size <= 0:
            continue
        candidate.replace(target)
        return target
    return target if target.exists() else None


def _nonempty_file(path: Path | None) -> bool:
    return bool(path and path.exists() and path.stat().st_size > 0)


def _scene_meta_dict(project_id: str, scene: Any) -> dict[str, Any]:
    idx = int(getattr(scene, "index", 0) or 0)
    scene_id = canonical_scene_id(idx)
    image_path = canonical_scene_image_path(project_id, idx)
    video_path = canonical_scene_video_path(project_id, idx)
    active_media_type = "video" if _nonempty_file(video_path) else "image"
    return {
        "scene_index": idx,
        "scene_id": str(getattr(scene, "scene_id", "") or scene_id),
        "canonical_scene_key": scene_id,
        "title": str(getattr(scene, "title", "") or ""),
        "excerpt": str(getattr(scene, "script_excerpt", "") or ""),
        "visual_intent": str(getattr(scene, "visual_intent", "") or ""),
        "prompt": str(getattr(scene, "image_prompt", "") or ""),
        "estimated_duration_sec": float(getattr(scene, "estimated_duration_sec", 0.0) or 0.0),
        "active_media_type": active_media_type,
        "asset_paths": {
            "image": str(image_path),
            "video": str(video_path),
            "effect": str(canonical_scene_effect_path(project_id, idx)),
        },
        "asset_urls": {
            "video_url": str(getattr(scene, "video_url", "") or ""),
        },
    }


def sync_scene_asset_metadata(project_id: str, scenes: list[Any] | None = None) -> list[Any]:
    loaded = list(scenes) if scenes is not None else load_scenes(project_id)
    pdir = project_dir(project_id)
    (pdir / "assets/effects").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/scene_meta").mkdir(parents=True, exist_ok=True)

    for scene in loaded:
        idx = int(getattr(scene, "index", 0) or 0)
        if idx <= 0:
            continue
        scene_key = canonical_scene_id(idx)

        image_target = canonical_scene_image_path(project_id, idx)
        image_alts = [pdir / "assets/images" / f"scene{idx:02d}.png", pdir / "assets/images" / f"{idx:02d}.png"]
        image_match = _normalize_existing_to_canonical(image_target, image_alts)

        raw_video = str(getattr(scene, "video_path", "") or "").strip()
        video_alts: list[Path] = [pdir / "assets/videos" / f"scene{idx:02d}.mp4"]
        if raw_video:
            video_alts.append(Path(raw_video).expanduser())
            if not Path(raw_video).is_absolute():
                video_alts.append(pdir / raw_video)
        video_target = canonical_scene_video_path(project_id, idx)
        video_match = _normalize_existing_to_canonical(video_target, video_alts)

        if _nonempty_file(video_match):
            scene.video_path = str(video_target)
            active_media_type = "video"
        else:
            scene.video_path = None
            active_media_type = "image"

        meta = _scene_meta_dict(project_id, scene)
        meta["active_media_type"] = active_media_type
        meta["asset_paths"]["image"] = str(image_target)
        meta["asset_paths"]["video"] = str(video_target)
        scene.active_media_type = active_media_type
        scene.asset_paths = dict(meta["asset_paths"])
        scene.asset_urls = dict(meta["asset_urls"])

        canonical_meta = canonical_scene_meta_path(project_id, idx)
        canonical_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # Keep stable scene_id for timeline determinism.
        if not str(getattr(scene, "scene_id", "") or "").strip() or str(getattr(scene, "scene_id", "")).startswith("scene-"):
            scene.scene_id = scene_key

        if image_match is None and scene.asset_paths.get("image"):
            scene.asset_paths["image"] = str(image_target)

    save_scenes(project_id, loaded)
    return loaded


def validate_project_assets(project_id: str) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {
        "missing_images": [],
        "missing_voiceover": [],
        "invalid_timeline_references": [],
        "empty_media_files": [],
        "stale_scene_media": [],
    }
    scenes = load_scenes(project_id)
    pdir = project_dir(project_id)

    voiceover_path = pdir / "assets/audio/voiceover.mp3"
    if not voiceover_path.exists() or voiceover_path.stat().st_size <= 0:
        issues["missing_voiceover"].append(str(voiceover_path))

    for scene in scenes:
        idx = int(getattr(scene, "index", 0) or 0)
        if idx <= 0:
            continue
        image_path = canonical_scene_image_path(project_id, idx)
        video_path = canonical_scene_video_path(project_id, idx)
        if not image_path.exists():
            issues["missing_images"].append(f"scene {idx}: {image_path}")
        elif image_path.stat().st_size <= 0:
            issues["empty_media_files"].append(str(image_path))

        raw_video_path = str(getattr(scene, "video_path", "") or "").strip()
        if raw_video_path:
            candidate = Path(raw_video_path)
            if not candidate.exists():
                issues["stale_scene_media"].append(f"scene {idx}: stale video_path={raw_video_path}")
            elif candidate.stat().st_size <= 0:
                issues["empty_media_files"].append(str(candidate))
            if candidate.exists() and candidate.resolve() != video_path.resolve():
                issues["stale_scene_media"].append(f"scene {idx}: non-canonical video={candidate}")

    timeline_path = pdir / "timeline.json"
    if timeline_path.exists():
        try:
            timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
            scene_count = len(scenes)
            for idx, tscene in enumerate(timeline.scenes, start=1):
                media = Path(tscene.image_path)
                if not str(tscene.id).startswith("s"):
                    issues["invalid_timeline_references"].append(f"scene {idx}: invalid id {tscene.id}")
                if idx > scene_count:
                    issues["invalid_timeline_references"].append(f"timeline scene {idx} has no source scene")
                if not str(tscene.image_path).startswith("storage://") and (not media.exists()):
                    issues["invalid_timeline_references"].append(f"scene {idx}: missing media {tscene.image_path}")
                elif media.exists() and media.stat().st_size <= 0:
                    issues["empty_media_files"].append(str(media))
        except Exception as exc:  # noqa: BLE001
            issues["invalid_timeline_references"].append(f"timeline parse error: {exc}")

    return issues


def regenerate_missing_scene_assets(project_id: str) -> dict[str, list[int]]:
    scenes = sync_scene_asset_metadata(project_id)
    results: dict[str, list[int]] = {"missing_images": [], "missing_video": [], "missing_scene_meta": []}
    for scene in scenes:
        idx = int(getattr(scene, "index", 0) or 0)
        if idx <= 0:
            continue
        if not canonical_scene_image_path(project_id, idx).exists():
            results["missing_images"].append(idx)
        if str(getattr(scene, "video_path", "") or "").strip() and not canonical_scene_video_path(project_id, idx).exists():
            results["missing_video"].append(idx)
        if not canonical_scene_meta_path(project_id, idx).exists():
            results["missing_scene_meta"].append(idx)
    return results


def rebuild_timeline_from_disk(project_id: str) -> Path:
    from src.workflow.services import PipelineOptions, run_sync_timeline

    sync_scene_asset_metadata(project_id)
    result = run_sync_timeline(project_id, PipelineOptions())
    if result.status.value != "completed":
        raise RuntimeError(result.message or "Failed to rebuild timeline")
    return Path(str(result.outputs.get("timeline_path", "")))


def preflight_report(project_id: str) -> dict[str, Any]:
    sync_scene_asset_metadata(project_id)
    issues = validate_project_assets(project_id)
    total = sum(len(v) for v in issues.values())
    actionable: list[str] = []
    if issues["missing_images"]:
        actionable.append("Generate images for listed scenes or restore canonical sNN.png files.")
    if issues["missing_voiceover"]:
        actionable.append("Generate voiceover or enable silent render fallback.")
    if issues["invalid_timeline_references"]:
        actionable.append("Run 'Rebuild Timeline from Disk' to repair scene/media references.")
    if issues["empty_media_files"]:
        actionable.append("Regenerate or replace empty media files.")
    if issues["stale_scene_media"]:
        actionable.append("Run 'Regenerate Missing Scene Assets' to re-canonicalize stale paths.")
    return {"ok": total == 0, "issue_count": total, "issues": issues, "actions": actionable}
