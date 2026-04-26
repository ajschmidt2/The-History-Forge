from src.ui.tabs.automation import _build_shorts_pipeline_options
from src.workflow.services import PipelineOptions


def test_build_shorts_pipeline_options_forces_subtitles_off() -> None:
    base = PipelineOptions(include_subtitles=True, include_music=False, aspect_ratio="16:9", number_of_scenes=8)

    resolved = _build_shorts_pipeline_options(base, "data/music library/track.mp3", "topic_to_short_video")

    assert resolved.include_subtitles is False
    assert resolved.include_music is True
    assert resolved.aspect_ratio == "9:16"
    assert resolved.number_of_scenes == 14
    assert resolved.selected_music_track == "data/music library/track.mp3"
