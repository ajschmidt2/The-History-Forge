from src.video.captions import build_ass_from_timeline
from src.video.timeline_schema import CaptionStyle, Meta, Scene, Timeline


def test_build_ass_from_timeline_preserves_line_breaks() -> None:
    timeline = Timeline(
        meta=Meta(
            project_id="p1",
            title="T",
            resolution="1080x1920",
            caption_style=CaptionStyle(font_size=48),
            include_voiceover=False,
            include_music=False,
        ),
        scenes=[
            Scene(id="s01", image_path="s01.png", start=0.0, duration=2.0, caption="Line one\nLine two"),
        ],
    )

    ass = build_ass_from_timeline(timeline)

    assert "Line one\\NLine two" in ass
    assert "{\\k" not in ass
