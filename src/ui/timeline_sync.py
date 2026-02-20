from pathlib import Path
from typing import Any

from src.video.timeline_builder import build_default_timeline, write_timeline_json
from src.video.timeline_schema import CaptionStyle, Timeline


def _scene_index_from_stem(stem: str, fallback: int) -> int:
    lowered = stem.lower()
    if lowered.startswith("s"):
        try:
            return int(lowered[1:])
        except ValueError:
            return fallback
    return fallback


def sync_timeline_for_project(
    project_path: Path,
    project_id: str,
    title: str,
    media_files: list[Path] | None = None,
    session_scenes: list[Any] | None = None,
    scene_captions: list[str] | None = None,
    meta_overrides: dict[str, Any] | None = None,
) -> Path | None:
    timeline_path = project_path / "timeline.json"
    images_dir = project_path / "assets/images"
    audio_dir = project_path / "assets/audio"
    music_dir = project_path / "assets/music"

    if media_files is None:
        media_files = sorted([p for p in images_dir.glob("*.*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
    if not media_files:
        return None

    existing_meta: dict[str, Any] = {}
    if timeline_path.exists():
        try:
            existing_meta = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8")).meta.model_dump()
        except ValueError:
            existing_meta = {}

    merged_meta = {**existing_meta, **(meta_overrides or {})}
    aspect_ratio = str(merged_meta.get("aspect_ratio", "16:9"))
    fps = int(merged_meta.get("fps", 30))
    burn_captions = bool(merged_meta.get("burn_captions", True))
    crossfade = bool(merged_meta.get("crossfade", False))
    crossfade_duration = float(merged_meta.get("crossfade_duration", 0.3))
    scene_duration = merged_meta.get("scene_duration")
    include_voiceover_requested = bool(merged_meta.get("include_voiceover", False))
    include_music_requested = bool(merged_meta.get("include_music", False))

    caption_style_payload = merged_meta.get("caption_style", {}) or {}
    try:
        caption_style = CaptionStyle(**caption_style_payload)
    except (TypeError, ValueError):
        caption_style = CaptionStyle()

    audio_files = sorted([p for p in audio_dir.glob("*.*") if p.suffix.lower() in {".wav", ".mp3"}])
    music_files = sorted([p for p in music_dir.glob("*.*") if p.suffix.lower() in {".wav", ".mp3"}])

    include_voiceover = include_voiceover_requested and bool(audio_files)
    include_music = include_music_requested and bool(music_files)

    music_volume_db = -18.0
    if isinstance(merged_meta.get("music"), dict):
        try:
            music_volume_db = float((merged_meta.get("music") or {}).get("volume_db", -18.0))
        except (TypeError, ValueError):
            music_volume_db = -18.0

    timeline = build_default_timeline(
        project_id=project_id,
        title=title,
        images=media_files,
        voiceover_path=audio_files[0] if include_voiceover else None,
        aspect_ratio=aspect_ratio,
        fps=fps,
        burn_captions=burn_captions,
        caption_style=caption_style,
        music_path=music_files[0] if include_music else None,
        music_volume_db=music_volume_db,
        include_voiceover=include_voiceover,
        include_music=include_music,
        enable_motion=True,
        crossfade=crossfade,
        crossfade_duration=crossfade_duration,
        scene_duration=float(scene_duration) if scene_duration is not None else None,
    )

    if scene_captions:
        for scene, caption in zip(timeline.scenes, scene_captions):
            scene.caption = str(caption or "").strip() or None
    elif session_scenes:
        excerpt_by_index: dict[int, str] = {}
        for scene in session_scenes:
            idx = getattr(scene, "index", None)
            excerpt = str(getattr(scene, "script_excerpt", "") or "").strip()
            if isinstance(idx, int) and excerpt:
                excerpt_by_index[idx] = excerpt
        for i, scene in enumerate(timeline.scenes, start=1):
            scene_index = _scene_index_from_stem(Path(scene.image_path).stem, i)
            scene.caption = excerpt_by_index.get(scene_index) or None

    write_timeline_json(timeline, timeline_path)
    return timeline_path
