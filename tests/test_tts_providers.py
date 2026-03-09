from src.audio.providers import (
    OPENAI_TTS_MODELS,
    OPENAI_TTS_VOICES,
    TTS_PROVIDER_ELEVENLABS,
    TTS_PROVIDER_OPENAI,
    resolve_tts_settings,
)


def test_resolve_tts_settings_defaults_to_elevenlabs():
    settings = resolve_tts_settings({})
    assert settings.provider == TTS_PROVIDER_ELEVENLABS
    assert settings.openai_tts_model in OPENAI_TTS_MODELS
    assert settings.openai_tts_voice in OPENAI_TTS_VOICES


def test_resolve_tts_settings_normalizes_openai_values():
    settings = resolve_tts_settings(
        {
            "tts_provider": TTS_PROVIDER_OPENAI,
            "openai_tts_model": "tts-1",
            "openai_tts_voice": "NOVA",
        }
    )
    assert settings.provider == TTS_PROVIDER_OPENAI
    assert settings.openai_tts_model == "tts-1"
    assert settings.openai_tts_voice == "nova"
