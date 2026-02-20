from pathlib import Path

from PIL import Image

from src.ui.tabs.video_studio import _render_subtitle_preview
from src.video.timeline_schema import CaptionStyle


def test_render_subtitle_preview_returns_image_bytes(tmp_path: Path) -> None:
    image_path = tmp_path / "scene.png"
    Image.new("RGB", (720, 1280), color=(20, 30, 40)).save(image_path)

    output = _render_subtitle_preview(
        image_path,
        "A sample caption for preview",
        caption_style=CaptionStyle(font_size=48, line_spacing=6, bottom_margin=120, position="lower"),
        burn_captions=True,
    )

    assert output[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(output) > 1000


def test_render_subtitle_preview_no_burn_keeps_image_visible(tmp_path: Path) -> None:
    image_path = tmp_path / "scene.png"
    Image.new("RGB", (320, 240), color=(90, 100, 110)).save(image_path)

    output = _render_subtitle_preview(
        image_path,
        "Ignored when burn captions off",
        caption_style=CaptionStyle(),
        burn_captions=False,
    )

    assert output[:8] == b"\x89PNG\r\n\x1a\n"
