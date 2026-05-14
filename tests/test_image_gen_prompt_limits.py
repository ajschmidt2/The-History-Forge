from __future__ import annotations

from image_gen import (
    DEFAULT_OPENAI_IMAGE_MODEL,
    _OPENAI_IMAGE_PROMPT_MAX_CHARS,
    _fit_openai_image_prompt,
    _normalize_openai_image_model,
)


def test_fit_openai_image_prompt_preserves_required_suffix_under_limit() -> None:
    prompt = "Cinematic historical scene. " + ("detailed period atmosphere " * 300)

    fitted = _fit_openai_image_prompt(prompt)

    assert len(fitted) <= _OPENAI_IMAGE_PROMPT_MAX_CHARS
    assert "Prompt shortened" in fitted
    assert "No visible text" in fitted
    assert "readable writing" in fitted


def test_fit_openai_image_prompt_leaves_short_prompt_unchanged() -> None:
    prompt = "Short documentary frame. No visible text."

    assert _fit_openai_image_prompt(prompt) == prompt


def test_fit_openai_image_prompt_hard_caps_oversized_caller_limit() -> None:
    prompt = "Cinematic historical scene. " + ("detailed period atmosphere " * 300)

    fitted = _fit_openai_image_prompt(prompt, max_chars=10_000)

    assert len(fitted) <= 3990
    assert "Prompt shortened" in fitted


def test_legacy_dall_e_3_image_model_uses_supported_default() -> None:
    assert _normalize_openai_image_model("dall-e-3") == DEFAULT_OPENAI_IMAGE_MODEL
    assert _normalize_openai_image_model(" dall-e-3 ") == DEFAULT_OPENAI_IMAGE_MODEL
