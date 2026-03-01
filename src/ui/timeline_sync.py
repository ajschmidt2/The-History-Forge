from pathlib import Path
from typing import Any
from urllib.request import urlopen
import time

from src.video.timeline_builder import build_default_timeline, write_timeline_json
from src.video.timeline_schema import CaptionStyle, Timeline
from src.ui.caption_format import format_caption


def _scene_index_from_stem(stem: str, fallback: int) -> int:
    lowered = stem.lower()
    if lowered.startswith("s"):
        try:
            return int(lowered[1:])
        except ValueError:
            return fallback
    return fallback


def _scene_number_from_path(path: Path) -> int | None:
    stem = path.stem.lower()
    if stem.startswith("s"):
        digits = "".join(ch for ch in stem[1:] if ch.isdigit())
        if digits:
            return int(digits)
    return None


def _media_sort_key(path: Path) -> tuple[int, int, str]:
    scene_number = _scene_number_from_path(path)
    if scene_number is not None:
        return (0, scene_number, path.name.lower())
    return (1, 10**9, path.name.lower())




def _persist_scene_video_url(project_path: Path, scene_index: int, video_url: str) -> Path | None:
    if not str(video_url or "").startswith(("http://", "https://")):
        return None
    videos_dir = project_path / "assets/videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    destination = videos_dir / f"s{scene_index:02d}_{int(time.time())}.mp4"
    try:
        with urlopen(video_url) as response:
            destination.write_bytes(response.read())
    except Exception:
        return None
    return destination

def _media_files_from_session_scenes(project_path: Path, session_scenes: list[Any]) -> list[Path]:
    images_dir = project_path / "assets/images"
    image_candidates = {p.stem.lower(): p for p in images_dir.glob("*.*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}}
    media_files: list[Path] = []
    for scene in session_scenes:
        idx = getattr(scene, "index", None)
        if not isinstance(idx, int) or idx <= 0:
            continue
        video_path = str(getattr(scene, "video_path", "") or "").strip()
        if video_path:
            candidate = Path(video_path)
            if candidate.exists() and candidate.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}:
                media_files.append(candidate)
                continue

        video_url = str(getattr(scene, "video_url", "") or "").strip()
        if video_url:
            downloaded = _persist_scene_video_url(project_path, idx, video_url)
            if downloaded and downloaded.exists():
                scene.video_path = str(downloaded)
                scene.video_url = None
                media_files.append(downloaded)
                continue

        preferred_stem = f"s{idx:02d}".lower()
        if preferred_stem in image_candidates:
            media_files.append(image_candidates[preferred_stem])
    return media_files


def _normalize_scene_captions(scene_captions: list[str] | None, expected_count: int) -> list[str]:
    captions = [str(caption or "") for caption in (scene_captions or [])[:expected_count]]
    if len(captions) < expected_count:
        captions.extend([""] * (expected_count - len(captions)))
    return captions


def _has_custom_transition(transition_types: list[str]) -> bool:
    return any(str(item or "").strip().lower() not in {"", "fade"} for item in transition_types)


def _caption_wrap_settings(aspect_ratio: str, font_size: int) -> tuple[int, int]:
    safe_font = max(18, int(font_size or 48))
    if str(aspect_ratio or "9:16") == "9:16":
        usable_width_px = 1080 - 160
        chars = max(12, min(28, int(usable_width_px / max(8.0, safe_font * 0.58))))
        return (14, chars)
    usable_width_px = 1920 - 200
    chars = max(22, min(52, int(usable_width_px / max(8.0, safe_font * 0.55))))
    return (12, chars)


def _apply_manual_scene_durations(
    timeline: Timeline,
    session_scenes: list[Any] | None,
    *,
    lock_total_duration_to_timeline: bool = False,
) -> None:
    if not session_scenes:
        return

    durations_by_index: dict[int, float] = {}
    for session_scene in session_scenes:
        idx = getattr(session_scene, "index", None)
        raw_duration = getattr(session_scene, "estimated_duration_sec", None)
        if not isinstance(idx, int):
            continue
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            continue
        if duration <= 0:
            continue
        durations_by_index[idx] = max(0.5, duration)

    if not durations_by_index:
        return

    target_total = float(timeline.total_duration)

    start = 0.0
    for i, scene in enumerate(timeline.scenes, start=1):
        scene_index = _scene_index_from_stem(Path(scene.image_path).stem, i)
        scene.duration = float(durations_by_index.get(scene_index, scene.duration))
        scene.start = start
        start += scene.duration

    if lock_total_duration_to_timeline and timeline.scenes and target_total > 0:
        current_total = sum(scene.duration for scene in timeline.scenes)
        if current_total > 0:
            scale = target_total / current_total
            for scene in timeline.scenes:
                scene.duration = max(0.1, float(scene.duration) * scale)

    start = 0.0
    for scene in timeline.scenes:
        scene.start = start
        start += scene.duration

    if timeline.scenes:
        timeline.meta.scene_duration = round(sum(scene.duration for scene in timeline.scenes) / len(timeline.scenes), 3)


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
        if session_scenes:
            media_files = _media_files_from_session_scenes(project_path, session_scenes)
        if not media_files:
            media_files = sorted([p for p in images_dir.glob("*.*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}], key=_media_sort_key)
    media_files = sorted(media_files, key=_media_sort_key)
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
    raw_transition_types = merged_meta.get("transition_types", [])
    transition_types = raw_transition_types if isinstance(raw_transition_types, list) else []
    effective_crossfade = crossfade or _has_custom_transition(transition_types)
    scene_duration = merged_meta.get("scene_duration")
    include_voiceover_requested = bool(merged_meta.get("include_voiceover", True))
    include_music_requested = bool(merged_meta.get("include_music", False))
    enable_motion = bool(merged_meta.get("enable_motion", True))
    narration_wpm = float(merged_meta.get("narration_wpm", 160))
    narration_min_sec = float(merged_meta.get("narration_min_sec", 1.5))
    narration_max_sec = float(merged_meta.get("narration_max_sec", 12.0))

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


    scene_excerpts: list[str] = []
    scene_video_options: dict[int, dict[str, float | bool]] = {}
    if session_scenes:
        for scene in session_scenes:
            idx = getattr(scene, "index", None)
            scene_excerpts.append(str(getattr(scene, "script_excerpt", "") or ""))
            if isinstance(idx, int) and idx > 0:
                scene_video_options[idx] = {
                    "video_loop": bool(getattr(scene, "video_loop", False)),
                    "video_muted": bool(getattr(scene, "video_muted", True)),
                    "video_volume": float(getattr(scene, "video_volume", 0.0) or 0.0),
                }

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
        enable_motion=enable_motion,
        crossfade=effective_crossfade,
        crossfade_duration=crossfade_duration,
        transition_types=transition_types,
        scene_duration=float(scene_duration) if scene_duration is not None else None,
        scene_excerpts=scene_excerpts,
        narration_wpm=narration_wpm,
        narration_min_sec=narration_min_sec,
        narration_max_sec=narration_max_sec,
        scene_video_options=scene_video_options,
    )

    _apply_manual_scene_durations(timeline, session_scenes, lock_total_duration_to_timeline=include_voiceover)

    normalized_captions = _normalize_scene_captions(scene_captions, len(media_files))
    if len(timeline.scenes) != len(normalized_captions):
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
            enable_motion=enable_motion,
            crossfade=effective_crossfade,
            crossfade_duration=crossfade_duration,
        transition_types=transition_types,
            scene_duration=float(scene_duration) if scene_duration is not None else None,
            scene_excerpts=scene_excerpts,
            narration_wpm=narration_wpm,
            narration_min_sec=narration_min_sec,
            narration_max_sec=narration_max_sec,
            scene_video_options=scene_video_options,
        )
        _apply_manual_scene_durations(timeline, session_scenes, lock_total_duration_to_timeline=include_voiceover)
    if len(timeline.scenes) != len(normalized_captions):
        raise ValueError(
            f"Caption mapping mismatch: {len(timeline.scenes)} timeline scenes vs {len(normalized_captions)} captions. "
            "Regenerate timeline and verify scene media ordering."
        )

    caption_max_lines, caption_max_chars = _caption_wrap_settings(aspect_ratio, caption_style.font_size)

    if normalized_captions:
        for scene, caption in zip(timeline.scenes, normalized_captions):
            formatted = format_caption(caption, max_lines=caption_max_lines, max_chars_per_line=caption_max_chars)
            scene.caption = formatted or None
    elif session_scenes:
        excerpt_by_index: dict[int, str] = {}
        for scene in session_scenes:
            idx = getattr(scene, "index", None)
            excerpt = str(getattr(scene, "script_excerpt", "") or "").strip()
            if isinstance(idx, int) and excerpt:
                excerpt_by_index[idx] = excerpt
        for i, scene in enumerate(timeline.scenes, start=1):
            scene_index = _scene_index_from_stem(Path(scene.image_path).stem, i)
            formatted = format_caption(excerpt_by_index.get(scene_index) or "", max_lines=caption_max_lines, max_chars_per_line=caption_max_chars)
            scene.caption = formatted or f"Scene {i}"

    for scene in timeline.scenes:
        if scene.caption is None:
            scene.caption = ""

    write_timeline_json(timeline, timeline_path)
    return timeline_path
