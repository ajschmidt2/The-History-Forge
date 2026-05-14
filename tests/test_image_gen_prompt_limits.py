from __future__ import annotations

from image_gen import _OPENAI_IMAGE_PROMPT_MAX_CHARS, _fit_openai_image_prompt


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
