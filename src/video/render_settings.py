from __future__ import annotations

from dataclasses import dataclass


ASPECT_RATIO_TO_RESOLUTION: dict[str, tuple[int, int]] = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
}

VIDEO_EFFECT_STYLE_OPTIONS: tuple[str, ...] = (
    "Off",
    "Ken Burns - Standard",
    "Ken Burns - Strong",
    "Ken Burns - Dramatic",
)


@dataclass(frozen=True)
class MotionPreset:
    zoom_min: float
    zoom_max: float
    pan_travel: float


def normalize_aspect_ratio(value: str | None, default: str = "9:16") -> str:
    ratio = str(value or default).strip()
    return ratio if ratio in ASPECT_RATIO_TO_RESOLUTION else default


def render_dimensions_for_aspect_ratio(aspect_ratio: str) -> tuple[int, int]:
    ratio = normalize_aspect_ratio(aspect_ratio)
    return ASPECT_RATIO_TO_RESOLUTION[ratio]


def render_resolution_for_aspect_ratio(aspect_ratio: str) -> str:
    width, height = render_dimensions_for_aspect_ratio(aspect_ratio)
    return f"{width}x{height}"


def normalize_video_effects_style(style: str | None, enable_motion: bool = True) -> str:
    raw = str(style or "").strip()
    if raw in VIDEO_EFFECT_STYLE_OPTIONS:
        return raw
    return "Ken Burns - Standard" if enable_motion else "Off"


def get_motion_preset(effect_style: str, aspect_ratio: str) -> MotionPreset | None:
    normalized_style = normalize_video_effects_style(effect_style)
    if normalized_style == "Off":
        return None

    # Slightly wider pan in vertical mode so motion remains visible.
    ratio_boost = 1.12 if normalize_aspect_ratio(aspect_ratio) == "9:16" else 1.0
    if normalized_style == "Ken Burns - Dramatic":
        return MotionPreset(zoom_min=1.08, zoom_max=1.24, pan_travel=0.22 * ratio_boost)
    if normalized_style == "Ken Burns - Strong":
        return MotionPreset(zoom_min=1.05, zoom_max=1.16, pan_travel=0.14 * ratio_boost)
    return MotionPreset(zoom_min=1.02, zoom_max=1.09, pan_travel=0.08 * ratio_boost)
