from src.video.captions import build_srt_from_timeline, write_srt
from src.video.timeline_schema import Meta, Scene, Timeline


def _sample_timeline() -> Timeline:
    return Timeline(
        meta=Meta(project_id="p1", title="T", include_voiceover=False, include_music=False),
        scenes=[
            Scene(id="s01", image_path="s01.png", start=0.0, duration=1.5, caption="First"),
            Scene(id="s02", image_path="s02.png", start=0.0, duration=2.0, caption="Second"),
            Scene(id="s03", image_path="s03.png", start=0.0, duration=3.0, caption="Third"),
        ],
    )


def test_build_srt_from_timeline_writes_one_cue_per_scene_with_cumulative_timing() -> None:
    srt_text = build_srt_from_timeline(_sample_timeline())

    assert "1\n00:00:00,000 --> 00:00:01,500\nFirst" in srt_text
    assert "2\n00:00:01,500 --> 00:00:03,500\nSecond" in srt_text
    assert "3\n00:00:03,500 --> 00:00:06,500\nThird" in srt_text
    assert srt_text.count(" --> ") == 3


def test_write_srt_writes_single_file_with_all_scene_cues(tmp_path) -> None:
    out_path = tmp_path / "captions.srt"
    write_srt(_sample_timeline(), out_path)

    content = out_path.read_text(encoding="utf-8")
    assert content.count(" --> ") == 3
    assert "Third" in content
