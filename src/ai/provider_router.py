from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# Task types routed to Ollama (cheap/utility/background)
_OLLAMA_TASKS = frozenset({
    "metadata", "cleanup", "tags", "cluster", "summary_draft",
    "json", "embedding", "topic",
})

# Task types routed to OpenAI (premium writing/polish)
_OPENAI_TASKS = frozenset({
    "script", "rewrite", "polish", "narration", "title", "description",
})


class ProviderRouter:
    def __init__(self, config) -> None:
        from src.ai.providers.ollama_provider import OllamaProvider
        from src.ai.providers.openai_provider import OpenAIProvider
        from src.ai.providers.gemini_provider import GeminiRouterProvider

        self._config = config
        self._ollama = OllamaProvider(
            base_url=config.ollama_base_url,
            text_model=config.ollama_text_model,
            json_model=config.ollama_json_model,
            embed_model=config.ollama_embed_model,
        )
        self._openai: OpenAIProvider | None = None
        if config.openai_api_key:
            self._openai = OpenAIProvider(
                api_key=config.openai_api_key,
                text_model=config.openai_text_model,
                fast_model=config.openai_fast_model,
                tts_model=config.openai_tts_model,
                tts_voice=config.openai_tts_voice,
            )
        self._gemini = GeminiRouterProvider(
            image_model=config.gemini_image_model,
            video_model=config.gemini_video_model,
        )

    # ------------------------------------------------------------------
    # Text generation
    # ------------------------------------------------------------------

    def generate_text(
        self,
        prompt: str,
        *,
        task_type: str = "default",
        system: str | None = None,
        temperature: float | None = None,
        quality: str = "standard",
    ) -> str:
        task = task_type.lower()
        t0 = time.monotonic()
        fallback_triggered = False

        if task in _OLLAMA_TASKS or (task not in _OPENAI_TASKS):
            try:
                result = self._ollama.chat(prompt, system=system, model=None)
                _log_call("ollama", self._config.ollama_text_model, task, True, t0)
                return result
            except Exception as exc:
                _LOG.warning("Ollama unavailable for task=%s: %s — falling back to OpenAI", task, exc)
                fallback_triggered = True

        # Premium tasks, or Ollama fallback
        if self._openai is None:
            raise RuntimeError(
                f"OpenAI not configured (OPENAI_API_KEY missing) and Ollama "
                f"unavailable for task={task!r}."
            )
        model = self._config.openai_text_model if quality == "high" else self._config.openai_fast_model
        kw: dict[str, Any] = {"system": system, "model": model}
        if temperature is not None:
            kw["temperature"] = temperature
        result = self._openai.generate_text(prompt, **kw)
        _log_call("openai", model, task, True, t0, fallback=fallback_triggered)
        return result

    # ------------------------------------------------------------------
    # Structured / JSON generation
    # ------------------------------------------------------------------

    def generate_structured(
        self,
        prompt: str,
        *,
        system: str | None = None,
        task_type: str = "json",
    ) -> str:
        """Return a raw JSON string. Ollama first, OpenAI on failure."""
        t0 = time.monotonic()
        try:
            result = self._ollama.structured(prompt, system=system)
            _log_call("ollama", self._config.ollama_json_model, task_type, True, t0)
            return result
        except Exception as exc:
            _LOG.warning("Ollama structured failed for task=%s: %s — falling back to OpenAI", task_type, exc)

        if self._openai is None:
            raise RuntimeError("OpenAI not configured and Ollama unavailable for structured generation.")
        result = self._openai.generate_structured(prompt, system=system)
        _log_call("openai", self._config.openai_fast_model, task_type, True, t0, fallback=True)
        return result

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        t0 = time.monotonic()
        result = self._ollama.embeddings(texts)
        _log_call("ollama", self._config.ollama_embed_model, "embedding", True, t0)
        return result

    # ------------------------------------------------------------------
    # Image generation
    # ------------------------------------------------------------------

    def generate_images(
        self,
        prompt: str,
        *,
        number_of_images: int = 1,
        aspect_ratio: str = "9:16",
        model: str | None = None,
    ) -> list[bytes]:
        t0 = time.monotonic()
        result = self._gemini.generate_images(
            prompt,
            number_of_images=number_of_images,
            aspect_ratio=aspect_ratio,
            model=model,
        )
        _log_call("gemini", model or self._config.gemini_image_model, "image", True, t0)
        return result

    # ------------------------------------------------------------------
    # Video generation
    # ------------------------------------------------------------------

    def generate_video_from_image(
        self,
        *,
        prompt: str,
        image_source: Any,
        output_path: str | Path,
        model: str | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: int = 8,
    ):
        t0 = time.monotonic()
        result = self._gemini.generate_video_from_image(
            prompt=prompt,
            image_source=image_source,
            output_path=output_path,
            model=model,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
        )
        _log_call("gemini", model or self._config.gemini_video_model, "video", result.ok, t0)
        return result

    # ------------------------------------------------------------------
    # Text-to-speech
    # ------------------------------------------------------------------

    def generate_speech(
        self,
        text: str,
        *,
        voice: str | None = None,
        model: str | None = None,
        instructions: str | None = None,
        output_format: str = "mp3",
        output_path: Path | None = None,
    ) -> tuple[bytes | None, str | None]:
        if self._openai is None:
            return None, "OpenAI not configured — OPENAI_API_KEY is required for TTS."
        t0 = time.monotonic()
        audio, err = self._openai.generate_speech(
            text,
            voice=voice,
            model=model,
            instructions=instructions,
            output_format=output_format,
            output_path=output_path,
        )
        _log_call("openai", model or self._config.openai_tts_model, "tts", err is None, t0)
        return audio, err


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_router: ProviderRouter | None = None


def get_router() -> ProviderRouter:
    global _router
    if _router is None:
        from src.config.ai_config import AIConfig
        _router = ProviderRouter(AIConfig())
    return _router


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _log_call(
    provider: str,
    model: str,
    task_type: str,
    success: bool,
    t0: float,
    *,
    fallback: bool = False,
) -> None:
    latency_ms = int((time.monotonic() - t0) * 1000)
    _LOG.info(
        "ai provider=%s model=%s task=%s success=%s latency_ms=%d fallback=%s",
        provider, model, task_type, success, latency_ms, fallback,
    )
