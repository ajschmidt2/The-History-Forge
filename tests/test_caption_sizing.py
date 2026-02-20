from src.video.captions import normalize_caption_style
from src.video.timeline_schema import CaptionStyle


def test_normalize_caption_style_upscales_legacy_small_font_sizes() -> None:
    style = CaptionStyle(font_size=10)
    normalized = normalize_caption_style(style, width=1080, height=1920)

    assert normalized.font_size == 50
    assert style.font_size == 10


def test_normalize_caption_style_keeps_modern_pixel_sizes() -> None:
    style = CaptionStyle(font_size=56)
    normalized = normalize_caption_style(style, width=1920, height=1080)

    assert normalized.font_size == 56
