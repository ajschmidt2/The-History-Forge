from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .timeline_schema import CaptionStyle, Ducking, Meta, Motion, Music, Scene, Timeline, Voiceover
from .render_settings import get_motion_preset, normalize_video_effects_style, render_resolution_for_aspect_ratio
from .utils import get_media_duration


def _scene_number_from_path(path: Path) -> int | None:
    match = re.search(r"s(\d+)", path.stem.lower())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _image_sort_key(path: Path) -> tuple[int, int, str]:
    scene_number = _scene_number_from_path(path)
    if scene_number is not None:
        return (0, scene_number, path.name.lower())
    return (1, 10**9, path.name.lower())


def _build_motion(index: int, effect_style: str, aspect_ratio: str) -> Motion | None:
    preset = get_motion_preset(effect_style, aspect_ratio)
    if preset is None:
        return None

    sweep_patterns = [
        (0.5 - preset.pan_travel / 2.0, 0.5 + preset.pan_travel / 2.0, 0.50, 0.50),
        (0.50, 0.50, 0.5 - preset.pan_travel / 2.0, 0.5 + preset.pan_travel / 2.0),
        (0.5 + preset.pan_travel / 2.0, 0.5 - preset.pan_travel / 2.0, 0.5 + preset.pan_travel / 3.0, 0.5 - preset.pan_travel / 3.0),
        (0.5 - preset.pan_travel / 3.0, 0.5 + preset.pan_travel / 3.0, 0.5 + preset.pan_travel / 2.0, 0.5 - preset.pan_travel / 2.0),
    ]
    x_start, x_end, y_start, y_end = sweep_patterns[(index - 1) % len(sweep_patterns)]
    zoom_in = index % 2 == 0
    zoom_start = preset.zoom_min if zoom_in else preset.zoom_max
    zoom_end = preset.zoom_max if zoom_in else preset.zoom_min
    return Motion(type="kenburns", zoom_start=zoom_start, zoom_end=zoom_end, x_start=x_start, x_end=x_end, y_start=y_start, y_end=y_end)

def compute_scene_durations(
    scenes: list[str],
    wpm: float = 160,
    min_sec: float = 1.5,
    max_sec: float = 12.0,
) -> list[float]:
    safe_wpm = max(1.0, float(wpm))
    words_per_second = safe_wpm / 60.0
    durations: list[float] = []
    for excerpt in scenes:
        word_count = len(str(excerpt or "").split())
        estimate = (word_count / words_per_second) if word_count > 0 else min_sec
        durations.append(max(float(min_sec), min(float(max_sec), float(estimate))))
    return durations



def build_default_timeline(
    project_id: str,
    title: str,
    images: Iterable[Path],
    voiceover_path: Path | None,
    aspect_ratio: str = "9:16",
    fps: int = 30,
    burn_captions: bool = True,
    caption_style: CaptionStyle | None = None,
    music_path: Path | None = None,
    music_volume_db: float = -18,
    include_voiceover: bool = True,
    include_music: bool = True,
    enable_motion: bool = True,
    video_effects_style: str = "Ken Burns - Standard",
    crossfade: bool = False,
    crossfade_duration: float = 0.3,
    transition_types: list[str] | None = None,
    scene_duration: float | None = None,
    scene_excerpts: list[str] | None = None,
    narration_wpm: float = 160,
    narration_min_sec: float = 1.5,
    narration_max_sec: float = 12.0,
    scene_video_options: dict[int, dict[str, float | bool]] | None = None,
) -> Timeline:
    image_list = list(images)
    if not image_list:
        raise ValueError("No scene images available to build a timeline.")

    if include_voiceover and voiceover_path is None:
        raise ValueError("Voiceover is enabled but no voiceover file was provided.")

    voiceover_duration = get_media_duration(voiceover_path) if voiceover_path else 0.0
    scene_count = len(image_list)

    if include_voiceover:
        excerpts = list(scene_excerpts or [])
        if len(excerpts) < scene_count:
            excerpts.extend([""] * (scene_count - len(excerpts)))
        scene_durations = compute_scene_durations(
            excerpts[:scene_count],
            wpm=narration_wpm,
            min_sec=narration_min_sec,
            max_sec=narration_max_sec,
        )
        if voiceover_duration > 0 and sum(scene_durations) > 0:
            scale = voiceover_duration / sum(scene_durations)
            scene_durations = [max(float(narration_min_sec), d * scale) for d in scene_durations]
            if sum(scene_durations) > 0 and voiceover_duration > 0:
                correction = voiceover_duration / sum(scene_durations)
                scene_durations = [d * correction for d in scene_durations]
    else:
        if scene_duration is None:
            scene_duration = 3.0
        scene_durations = [float(scene_duration)] * scene_count

    scenes: list[Scene] = []
    current_start = 0.0

    for idx, image_path in enumerate(image_list, start=1):
        duration = scene_durations[idx - 1] if idx - 1 < len(scene_durations) else float(scene_duration or 3.0)
        video_options = (scene_video_options or {}).get(idx, {})
        scenes.append(
            Scene(
                id=f"s{idx:02d}",
                image_path=str(image_path),
                start=round(current_start, 3),
                duration=round(duration, 3),
                motion=_build_motion(idx, normalize_video_effects_style(video_effects_style, enable_motion=enable_motion), aspect_ratio) if enable_motion else None,
                caption=None,
                video_loop=bool(video_options.get("video_loop", False)),
                video_muted=bool(video_options.get("video_muted", True)),
                video_volume=float(video_options.get("video_volume", 0.0) or 0.0),
            )
        )
        current_start += duration

    music = None
    if music_path:
        music = Music(path=str(music_path), volume_db=music_volume_db, ducking=Ducking(enabled=False))

    timeline = Timeline(
        meta=Meta(
            project_id=project_id,
            title=title,
            aspect_ratio=aspect_ratio,
            resolution=render_resolution_for_aspect_ratio(aspect_ratio),
            fps=fps,
            scene_duration=(round(sum(scene_durations) / scene_count, 3) if scene_durations else scene_duration),
            burn_captions=burn_captions,
            include_voiceover=include_voiceover,
            include_music=include_music,
            crossfade=crossfade,
            crossfade_duration=crossfade_duration,
            video_effects_style=normalize_video_effects_style(video_effects_style, enable_motion=enable_motion),
            transition_types=list(transition_types or []),
            narration_wpm=narration_wpm,
            narration_min_sec=narration_min_sec,
            narration_max_sec=narration_max_sec,
            caption_style=caption_style or CaptionStyle(),
            music=music,
            voiceover=Voiceover(path=str(voiceover_path)) if voiceover_path else None,
        ),
        scenes=scenes,
    )
    return timeline


def write_timeline_json(timeline: Timeline, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
    return output_path
