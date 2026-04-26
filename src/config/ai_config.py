from __future__ import annotations

from dataclasses import dataclass, field

from src.config import get_secret


def _s(key: str, *fallbacks: str, default: str = "") -> str:
    """Resolve the first non-empty value across key aliases via the central secrets layer."""
    for k in (key,) + fallbacks:
        v = str(get_secret(k, "") or "").strip()
        if v:
            return v
    return default


@dataclass
class AIConfig:
    # Ollama — local default provider for cheap/utility text tasks
    ollama_base_url: str = field(default_factory=lambda: _s("OLLAMA_BASE_URL", default="http://localhost:11434"))
    ollama_text_model: str = field(default_factory=lambda: _s("OLLAMA_TEXT_MODEL", default="qwen3.5:9b"))
    ollama_json_model: str = field(default_factory=lambda: _s("OLLAMA_JSON_MODEL", default="qwen3.5:9b"))
    ollama_embed_model: str = field(default_factory=lambda: _s("OLLAMA_EMBED_MODEL", default="embeddinggemma"))

    # OpenAI — premium writing, narration, TTS
    # Respects existing OPENAI_MODEL key for backward compat, with new keys taking priority.
    openai_api_key: str = field(default_factory=lambda: _s("OPENAI_API_KEY", "openai_api_key"))
    openai_text_model: str = field(default_factory=lambda: _s("OPENAI_TEXT_MODEL", "OPENAI_MODEL", "openai_model", default="gpt-4o"))
    openai_fast_model: str = field(default_factory=lambda: _s("OPENAI_FAST_MODEL", default="gpt-4o-mini"))
    openai_tts_model: str = field(default_factory=lambda: _s("OPENAI_TTS_MODEL", default="gpt-4o-mini-tts"))
    openai_tts_voice: str = field(default_factory=lambda: _s("OPENAI_TTS_VOICE", default="alloy"))

    # Gemini — image and video generation only
    gemini_api_key: str = field(default_factory=lambda: _s("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    gemini_image_model: str = field(default_factory=lambda: _s("GEMINI_IMAGE_MODEL", default="gemini-2.5-flash-image"))
    gemini_video_model: str = field(default_factory=lambda: _s("GEMINI_VIDEO_MODEL", default="veo-3.1-lite-generate-preview"))
