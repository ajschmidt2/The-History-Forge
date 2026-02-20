from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, validator


class CaptionStyle(BaseModel):
    font: str = "Arial"
    font_size: int = 12
    line_spacing: int = 6
    bottom_margin: int = 140
    position: str = "lower"

    @validator("position")
    def validate_position(cls, value: str) -> str:
        if value not in {"lower", "center", "top"}:
            raise ValueError("position must be 'lower', 'center', or 'top'")
        return value


class Ducking(BaseModel):
    enabled: bool = True
    threshold_db: float = -28
    ratio: float = 8
    attack: int = 15
    release: int = 250


class Music(BaseModel):
    path: Optional[str] = None
    volume_db: float = -18
    ducking: Optional[Ducking] = None


class Voiceover(BaseModel):
    path: str
    loudnorm: bool = True
    target_i: float = -16
    true_peak: float = -1.5
    lra: float = 11


class Motion(BaseModel):
    type: str = "kenburns"
    zoom_start: float = 1.03
    zoom_end: float = 1.10
    x_start: float = 0.5
    y_start: float = 0.5
    x_end: float = 0.5
    y_end: float = 0.5
    x: Optional[float] = None
    y: Optional[float] = None


class Scene(BaseModel):
    id: str
    image_path: str
    start: float
    duration: float
    motion: Optional[Motion] = None
    caption: Optional[str] = None

    @property
    def end(self) -> float:
        return self.start + self.duration


class Meta(BaseModel):
    project_id: str
    title: str
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    fps: int = 30
    scene_duration: Optional[float] = None
    burn_captions: bool = True
    include_voiceover: bool = True
    include_music: bool = True
    enable_motion: bool = True
    crossfade: bool = False
    crossfade_duration: float = 0.3
    transition_types: List[str] = Field(default_factory=list)
    narration_wpm: float = 160
    narration_min_sec: float = 1.5
    narration_max_sec: float = 12.0
    caption_style: CaptionStyle = Field(default_factory=CaptionStyle)
    music: Optional[Music] = None
    voiceover: Optional[Voiceover] = None

    @validator("aspect_ratio")
    def validate_aspect_ratio(cls, value: str) -> str:
        if value not in {"9:16", "16:9"}:
            raise ValueError("aspect_ratio must be '9:16' or '16:9'")
        return value


class Timeline(BaseModel):
    meta: Meta
    scenes: List[Scene]

    @property
    def total_duration(self) -> float:
        if not self.scenes:
            return 0.0
        return max(scene.end for scene in self.scenes)
