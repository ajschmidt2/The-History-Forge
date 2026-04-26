from __future__ import annotations

"""Thin router-facing wrapper around src.providers.gemini_provider.

All heavy logic lives in the original module. This wrapper exposes the
interface expected by ProviderRouter without duplicating implementation.
"""

from pathlib import Path
from typing import Any

import src.providers.gemini_provider as _gemini


class GeminiRouterProvider:
    def __init__(self, image_model: str, video_model: str) -> None:
        self.image_model = image_model
        self.video_model = video_model

    def generate_images(
        self,
        prompt: str,
        *,
        number_of_images: int = 1,
        aspect_ratio: str = "9:16",
        model: str | None = None,
    ) -> list[bytes]:
        return _gemini.generate_images(
            prompt,
            model=model or self.image_model,
            number_of_images=number_of_images,
            aspect_ratio=aspect_ratio,
        )

    def generate_video_from_image(
        self,
        *,
        prompt: str,
        image_source: Any,
        output_path: str | Path,
        model: str | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: int = 8,
    ) -> _gemini.GeminiVideoResult:
        return _gemini.generate_video_from_image(
            prompt=prompt,
            image_source=image_source,
            output_path=output_path,
            model=model or self.video_model,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
        )

    def generate_text(self, prompt: str, *, model: str | None = None, system_instruction: str | None = None) -> str:
        return _gemini.generate_text(prompt, model=model, system_instruction=system_instruction)
