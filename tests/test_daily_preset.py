from pathlib import Path

from src.workflow.daily_job import load_daily_automation_settings, save_daily_automation_settings
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


def test_daily_automation_settings_roundtrip(tmp_path: Path):
    settings_path = tmp_path / "daily_automation_settings.json"

    save_daily_automation_settings(
        {
            "topic_override": "Roman Roads",
            "selected_music_track": "data/music_library/ambient.mp3",
            "preset": {
                "scene_count": 20,
                "target_word_count": 180,
                "subtitles_enabled": True,
            },
        },
        path=settings_path,
    )

    saved = load_daily_automation_settings(path=settings_path)
    assert saved["topic_override"] == "Roman Roads"
    assert saved["selected_music_track"] == "data/music_library/ambient.mp3"
    assert saved["preset"]["scene_count"] == 20
    assert saved["preset"]["target_word_count"] == 180
    assert saved["preset"]["subtitles_enabled"] is True
    assert saved["preset"]["openai_tts_voice"] == DAILY_SHORT_PRESET.openai_tts_voice
