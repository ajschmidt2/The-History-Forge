from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .timeline_schema import CaptionStyle, Ducking, Meta, Motion, Music, Scene, Timeline, Voiceover
from .utils import get_media_duration


def _resolution_for_aspect_ratio(aspect_ratio: str) -> str:
    if aspect_ratio == "16:9":
        return "1920x1080"
    return "1080x1920"


def _build_motion(index: int) -> Motion:
    zoom_in = index % 2 == 0
    if zoom_in:
        return Motion(
            type="kenburns",
            zoom_start=1.03,
            zoom_end=1.1,
            x_start=0.48,
            x_end=0.52,
            y_start=0.5,
            y_end=0.5,
        )
    return Motion(
        type="kenburns",
        zoom_start=1.1,
        zoom_end=1.03,
        x_start=0.52,
        x_end=0.48,
        y_start=0.5,
        y_end=0.5,
    )


def build_default_timeline(
    project_id: str,
    title: str,
    images: Iterable[Path],
    voiceover_path: Path | None,
    aspect_ratio: str = "9:16",
    fps: int = 30,
    burn_captions: bool = True,
    music_path: Path | None = None,
    music_volume_db: float = -18,
    include_voiceover: bool = True,
    include_music: bool = True,
    enable_motion: bool = True,
    crossfade: bool = False,
    crossfade_duration: float = 0.3,
) -> Timeline:
    image_list = list(images)
    if not image_list:
        raise ValueError("No scene images available to build a timeline.")

    if include_voiceover and voiceover_path is None:
        raise ValueError("Voiceover is enabled but no voiceover file was provided.")

    voiceover_duration = get_media_duration(voiceover_path) if voiceover_path else 0.0
    scene_count = len(image_list)
    if aspect_ratio == "9:16" and scene_count > 18:
        image_list = image_list[:18]
        scene_count = len(image_list)

    if voiceover_duration > 0:
        scene_duration = voiceover_duration / scene_count
    else:
        scene_duration = 3.0
    scenes: list[Scene] = []
    current_start = 0.0

    for idx, image_path in enumerate(image_list, start=1):
        scenes.append(
            Scene(
                id=f"s{idx:02d}",
                image_path=str(image_path),
                start=round(current_start, 3),
                duration=round(scene_duration, 3),
                motion=_build_motion(idx) if enable_motion else None,
                caption=None,
            )
        )
        current_start += scene_duration

    music = None
    if music_path:
        music = Music(path=str(music_path), volume_db=music_volume_db, ducking=Ducking())

    timeline = Timeline(
        meta=Meta(
            project_id=project_id,
            title=title,
            aspect_ratio=aspect_ratio,
            resolution=_resolution_for_aspect_ratio(aspect_ratio),
            fps=fps,
            burn_captions=burn_captions,
            include_voiceover=include_voiceover,
            include_music=include_music,
            crossfade=crossfade,
            crossfade_duration=crossfade_duration,
            caption_style=CaptionStyle(),
            music=music,
            voiceover=Voiceover(path=str(voiceover_path)) if voiceover_path else None,
        ),
        scenes=scenes,
    )
    return timeline


def write_timeline_json(timeline: Timeline, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(timeline.json(indent=2), encoding="utf-8")
    return output_path
