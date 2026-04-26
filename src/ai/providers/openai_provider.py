from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)


class OpenAIProvider:
    def __init__(self, api_key: str, text_model: str, fast_model: str, tts_model: str, tts_voice: str) -> None:
        if not api_key:
            raise RuntimeError("Missing OPENAI_API_KEY — cannot initialise OpenAI provider.")
        self._api_key = api_key
        self.text_model = text_model
        self.fast_model = fast_model
        self.tts_model = tts_model
        self.tts_voice = tts_voice

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=self._api_key)

    def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        response_format: dict | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": model or self.text_model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            kwargs["response_format"] = response_format

        client = self._client()
        response = client.chat.completions.create(**kwargs)
        return str(response.choices[0].message.content or "").strip()

    def generate_structured(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Return raw JSON string using JSON mode."""
        return self.generate_text(
            prompt,
            system=system,
            model=model or self.fast_model,
            temperature=0.25,
            response_format={"type": "json_object"},
        )

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
        from src.audio.providers import generate_openai_voiceover
        return generate_openai_voiceover(
            text=text,
            model=model or self.tts_model,
            voice=voice or self.tts_voice,
            instructions=instructions,
            output_format=output_format,
            output_path=output_path,
        )
