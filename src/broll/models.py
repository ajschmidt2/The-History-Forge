"""Normalized B-roll result model shared across all providers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BrollResult:
    """Normalized representation of a free stock video clip from any provider.

    All provider-specific responses are mapped into this model so the rest of
    the application can work with a single consistent interface regardless of
    which API returned the clip.
    """

    provider: str
    """Which API returned this result: 'pexels' or 'pixabay'."""

    id: str
    """Provider-specific unique identifier for the clip."""

    title: str
    """Human-readable title or comma-joined tags for the clip."""

    duration_sec: float
    """Duration of the clip in seconds."""

    width: int
    """Video width in pixels for the best available file."""

    height: int
    """Video height in pixels for the best available file."""

    orientation: str
    """'horizontal' (landscape / 16:9) or 'vertical' (portrait / 9:16)."""

    preview_image_url: str
    """URL to a still thumbnail suitable for display in the UI."""

    video_url: str
    """Direct URL to the video file that should be downloaded."""

    page_url: str
    """Link to the original clip page for attribution."""

    attribution_text: str
    """Human-readable attribution string (e.g. 'Photo by Name on Pexels')."""

    license_note: str
    """Short description of the license (e.g. 'Pexels License – free to use')."""

    local_path: str = ""
    """Absolute local path once the clip has been downloaded; empty until then."""

    def __post_init__(self) -> None:
        self.duration_sec = float(self.duration_sec or 0.0)
        self.width = int(self.width or 0)
        self.height = int(self.height or 0)

    @property
    def aspect_ratio_label(self) -> str:
        """Returns '9:16' or '16:9' based on orientation."""
        return "9:16" if self.orientation == "vertical" else "16:9"

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "id": self.id,
            "title": self.title,
            "duration_sec": self.duration_sec,
            "width": self.width,
            "height": self.height,
            "orientation": self.orientation,
            "preview_image_url": self.preview_image_url,
            "video_url": self.video_url,
            "page_url": self.page_url,
            "attribution_text": self.attribution_text,
            "license_note": self.license_note,
            "local_path": self.local_path,
        }
