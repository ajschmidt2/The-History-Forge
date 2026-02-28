import pytest

import utils
from src.lib import openai_config


def _secret_reader(mapping):
    def _reader(name, default=""):
        return mapping.get(name, mapping.get(name.upper(), default))

    return _reader


def test_resolve_openai_config_rejects_api_key_like_model():
    openai_config.resolve_openai_config.cache_clear()
    with pytest.raises(ValueError, match="OPENAI_MODEL is an API key"):
        openai_config.resolve_openai_config(
            get_secret=_secret_reader(
                {
                    "openai_api_key": "sk-proj-real-key",
                    "openai_model": "sk-proj-abc123",
                }
            )
        )


def test_resolve_openai_config_uses_default_model():
    openai_config.resolve_openai_config.cache_clear()
    cfg = openai_config.resolve_openai_config(
        get_secret=_secret_reader({"openai_api_key": "sk-proj-real-key"})
    )
    assert cfg.model == "gpt-5-mini"


def test_get_openai_text_model_accepts_regular_model_id(monkeypatch):
    monkeypatch.setattr(utils, "_get_secret", lambda name, default="": {
        "openai_api_key": "sk-proj-real-key",
        "openai_model": "gpt-5-mini",
    }.get(name, default))
    openai_config.resolve_openai_config.cache_clear()

    assert utils.get_openai_text_model(default="gpt-5-mini") == "gpt-5-mini"


def test_resolve_openai_config_requires_api_key():
    openai_config.resolve_openai_config.cache_clear()
    with pytest.raises(ValueError, match="Missing OPENAI_API_KEY"):
        openai_config.resolve_openai_config(get_secret=_secret_reader({"openai_model": "gpt-5-mini"}))
