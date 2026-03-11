from src.workflow.presets import DAILY_SHORT_PRESET


def test_daily_short_preset_pipeline_defaults():
    options = DAILY_SHORT_PRESET.to_pipeline_options(topic="Test", selected_music_track="data/music_library/test.mp3")

    assert options.aspect_ratio == "9:16"
    assert options.number_of_scenes == 14
    assert options.include_subtitles is False
    assert options.include_music is True
    assert options.music_volume_relative_to_voiceover == 0.15
    assert options.tts_provider == "openai"
    assert options.openai_tts_model == "gpt-4o-mini-tts"
    assert options.openai_tts_voice == "ash"
