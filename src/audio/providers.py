from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.config import resolve_openai_key
from utils import generate_voiceover as generate_elevenlabs_voiceover

TTS_PROVIDER_ELEVENLABS = "elevenlabs"
TTS_PROVIDER_OPENAI = "openai"

OPENAI_TTS_MODEL_GPT4O_MINI = "gpt-4o-mini-tts"
OPENAI_TTS_MODELS: tuple[str, ...] = (
    OPENAI_TTS_MODEL_GPT4O_MINI,
    "tts-1",
    "tts-1-hd",
)

OPENAI_TTS_VOICES: tuple[str, ...] = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
)


@dataclass(slots=True)
class TTSSettings:
    provider: str = TTS_PROVIDER_ELEVENLABS
    elevenlabs_voice_id: str = ""
    openai_tts_model: str = OPENAI_TTS_MODEL_GPT4O_MINI
    openai_tts_voice: str = "alloy"
    openai_tts_instructions: str = ""
    output_format: str = "mp3"


def get_tts_provider_options() -> list[str]:
    return [TTS_PROVIDER_ELEVENLABS, TTS_PROVIDER_OPENAI]


def get_openai_tts_models() -> list[str]:
    return list(OPENAI_TTS_MODELS)


def get_openai_tts_voices() -> list[str]:
    return list(OPENAI_TTS_VOICES)


def resolve_tts_settings(raw: dict[str, Any] | None = None, **overrides: Any) -> TTSSettings:
    payload = dict(raw or {})
    payload.update({key: value for key, value in overrides.items() if value is not None})

    provider = str(payload.get("tts_provider", payload.get("provider", TTS_PROVIDER_ELEVENLABS)) or "").strip().lower()
    if provider not in get_tts_provider_options():
        provider = TTS_PROVIDER_ELEVENLABS

    model = str(payload.get("openai_tts_model", OPENAI_TTS_MODEL_GPT4O_MINI) or "").strip() or OPENAI_TTS_MODEL_GPT4O_MINI
    if model not in OPENAI_TTS_MODELS:
        model = OPENAI_TTS_MODEL_GPT4O_MINI

    voice = str(payload.get("openai_tts_voice", "alloy") or "").strip().lower() or "alloy"
    if voice not in OPENAI_TTS_VOICES:
        voice = "alloy"

    return TTSSettings(
        provider=provider,
        elevenlabs_voice_id=str(payload.get("elevenlabs_voice_id", payload.get("voice_id", "")) or "").strip(),
        openai_tts_model=model,
        openai_tts_voice=voice,
        openai_tts_instructions=str(payload.get("openai_tts_instructions", "") or "").strip(),
        output_format=str(payload.get("output_format", "mp3") or "mp3").strip().lower(),
    )


def generate_openai_voiceover(
    text: str,
    model: str = OPENAI_TTS_MODEL_GPT4O_MINI,
    voice: str = "alloy",
    instructions: str | None = None,
    output_format: str = "mp3",
) -> tuple[bytes | None, str | None]:
    prompt = str(text or "").strip()
    if not prompt:
        return None, "Script is empty."

    api_key = resolve_openai_key().strip()
    if not api_key:
        return None, "[Missing OPENAI_API_KEY] Add it in Streamlit Secrets."

    chosen_model = str(model or OPENAI_TTS_MODEL_GPT4O_MINI).strip() or OPENAI_TTS_MODEL_GPT4O_MINI
    if chosen_model not in OPENAI_TTS_MODELS:
        return None, f"Invalid OpenAI TTS model: {chosen_model}"

    chosen_voice = str(voice or "alloy").strip().lower() or "alloy"
    if chosen_voice not in OPENAI_TTS_VOICES:
        return None, f"Invalid OpenAI TTS voice: {chosen_voice}"

    try:
        from openai import APIConnectionError, APIError, AuthenticationError, BadRequestError, OpenAI
    except Exception as exc:  # noqa: BLE001
        return None, f"OpenAI SDK import failed: {exc}"

    client = OpenAI(api_key=api_key)
    kwargs: dict[str, Any] = {
        "model": chosen_model,
        "voice": chosen_voice,
        "input": prompt,
        "format": str(output_format or "mp3").strip().lower() or "mp3",
    }
    if chosen_model == OPENAI_TTS_MODEL_GPT4O_MINI and str(instructions or "").strip():
        kwargs["instructions"] = str(instructions or "").strip()

    try:
        response = client.audio.speech.create(**kwargs)
        audio_bytes = getattr(response, "content", None)
        if not audio_bytes and hasattr(response, "read") and callable(response.read):
            audio_bytes = response.read()
        if not isinstance(audio_bytes, (bytes, bytearray)) or len(audio_bytes) == 0:
            return None, "OpenAI TTS returned empty audio content."
        return bytes(audio_bytes), None
    except AuthenticationError:
        return None, "OpenAI authentication failed. Check OPENAI_API_KEY."
    except BadRequestError as exc:
        detail = str(exc)
        lowered = detail.lower()
        if "model" in lowered:
            return None, f"Invalid OpenAI TTS model: {chosen_model}. Detail: {detail}"
        if "voice" in lowered:
            return None, f"Invalid OpenAI TTS voice: {chosen_voice}. Detail: {detail}"
        return None, f"OpenAI TTS request failed: {detail}"
    except APIConnectionError as exc:
        return None, f"OpenAI TTS connection failed: {exc}"
    except APIError as exc:
        return None, f"OpenAI TTS API error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, f"OpenAI TTS request failed: {exc}"


def generate_voiceover_with_provider(text: str, settings: TTSSettings) -> tuple[bytes | None, str | None]:
    if settings.provider == TTS_PROVIDER_OPENAI:
        return generate_openai_voiceover(
            text=text,
            model=settings.openai_tts_model,
            voice=settings.openai_tts_voice,
            instructions=settings.openai_tts_instructions,
            output_format=settings.output_format,
        )

    return generate_elevenlabs_voiceover(
        script=text,
        voice_id=settings.elevenlabs_voice_id,
        output_format=settings.output_format,
    )
