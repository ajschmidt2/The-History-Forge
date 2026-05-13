import pytest

import src.config.secrets as secrets


def test_validate_fal_key_rules():
    assert not secrets.validate_fal_key("")
    assert not secrets.validate_fal_key("no-colon-here")
    assert secrets.validate_fal_key("fal:abc123456789")


def test_get_fal_key_prefers_root_level_streamlit_secret(monkeypatch):
    monkeypatch.setattr(
        secrets,
        "_safe_streamlit_secrets",
        lambda: {
            "FAL_KEY": "fal:root-level-123456",
            "fal": {"fal_api_key": "fal:nested-should-not-win"},
        },
    )
    monkeypatch.delenv("FAL_KEY", raising=False)

    resolved = secrets.get_fal_key()

    assert resolved == "fal:root-level-123456"
    assert secrets.os.environ["FAL_KEY"] == "fal:root-level-123456"


def test_get_fal_key_reads_nested_streamlit_secret(monkeypatch):
    monkeypatch.setattr(
        secrets,
        "_safe_streamlit_secrets",
        lambda: {"api_keys": {"fal_api_key": "fal:nested-123456"}},
    )
    monkeypatch.delenv("FAL_KEY", raising=False)

    assert secrets.get_fal_key() == "fal:nested-123456"


def test_get_fal_key_env_priority_and_error(monkeypatch):
    monkeypatch.setattr(secrets, "_safe_streamlit_secrets", lambda: None)
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("fal_key", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    monkeypatch.delenv("fal_api_key", raising=False)

    monkeypatch.setenv("fal_key", "fal:lowercase-123456")
    monkeypatch.setenv("FAL_API_KEY", "fal:upper-api-should-lose")
    assert secrets.get_fal_key() == "fal:lowercase-123456"

    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("fal_key", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="fal.ai API key not found"):
        secrets.get_fal_key()


def test_fal_video_model_registry_includes_default():
    from src.services.fal_video_test import DEFAULT_FAL_VIDEO_MODEL, FAL_VIDEO_MODELS

    model_slugs = [slug for slug, _label in FAL_VIDEO_MODELS]

    assert DEFAULT_FAL_VIDEO_MODEL in model_slugs
    assert all(slug.startswith("fal-ai/") for slug in model_slugs)
    assert all(label for _slug, label in FAL_VIDEO_MODELS)


def test_openai_image_model_registry_includes_default():
    from image_gen import DEFAULT_OPENAI_IMAGE_MODEL, OPENAI_IMAGE_MODELS

    model_slugs = [slug for slug, _label in OPENAI_IMAGE_MODELS]

    assert DEFAULT_OPENAI_IMAGE_MODEL in model_slugs
    assert all(slug for slug in model_slugs)
    assert all(label for _slug, label in OPENAI_IMAGE_MODELS)
