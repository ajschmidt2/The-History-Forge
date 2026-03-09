from pathlib import Path

import src.audio.providers as providers
from src.audio.providers import (
    OPENAI_TTS_MODELS,
    OPENAI_TTS_VOICES,
    TTS_PROVIDER_ELEVENLABS,
    TTS_PROVIDER_OPENAI,
    generate_openai_voiceover,
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


def test_generate_openai_voiceover_uses_response_format_and_streaming(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    output_path = tmp_path / "voiceover.mp3"

    class _StreamingResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream_to_file(self, path: str):
            Path(path).write_bytes(b"audio")

    class _SpeechWithStreaming:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return _StreamingResponse()

    class _Speech:
        with_streaming_response = _SpeechWithStreaming()

    class _Audio:
        speech = _Speech()

    class _OpenAI:
        def __init__(self, api_key: str):
            self.audio = _Audio()

    class _OpenAIModule:
        APIConnectionError = RuntimeError
        APIError = RuntimeError
        AuthenticationError = RuntimeError
        BadRequestError = RuntimeError
        OpenAI = _OpenAI

    monkeypatch.setitem(__import__("sys").modules, "openai", _OpenAIModule())
    monkeypatch.setattr(providers, "resolve_openai_key", lambda: "test-key")

    audio, err = generate_openai_voiceover(
        text="Narration",
        model="gpt-4o-mini-tts",
        voice="alloy",
        instructions="Warm tone",
        output_format="mp3",
        output_path=output_path,
    )

    assert err is None
    assert audio == b"audio"
    assert captured["response_format"] == "mp3"
    assert "format" not in captured
    assert captured["instructions"] == "Warm tone"


def test_generate_openai_voiceover_omits_instructions_for_tts1(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _Response:
        content = b"audio"

    class _Speech:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return _Response()

    class _Audio:
        speech = _Speech()

    class _OpenAI:
        def __init__(self, api_key: str):
            self.audio = _Audio()

    class _OpenAIModule:
        APIConnectionError = RuntimeError
        APIError = RuntimeError
        AuthenticationError = RuntimeError
        BadRequestError = RuntimeError
        OpenAI = _OpenAI

    monkeypatch.setitem(__import__("sys").modules, "openai", _OpenAIModule())
    monkeypatch.setattr(providers, "resolve_openai_key", lambda: "test-key")

    audio, err = generate_openai_voiceover(
        text="Narration",
        model="tts-1",
        voice="alloy",
        instructions="Should be omitted",
    )

    assert err is None
    assert audio == b"audio"
    assert "instructions" not in captured
