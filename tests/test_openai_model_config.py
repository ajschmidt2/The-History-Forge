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
    assert cfg.model == "gpt-4o-mini"


def test_get_openai_text_model_accepts_regular_model_id(monkeypatch):
    monkeypatch.setattr(utils, "_get_secret", lambda name, default="": {
        "openai_api_key": "sk-proj-real-key",
        "openai_model": "gpt-4o-mini",
    }.get(name, default))
    openai_config.resolve_openai_config.cache_clear()

    assert utils.get_openai_text_model(default="gpt-4o-mini") == "gpt-4o-mini"


def test_resolve_openai_config_requires_api_key():
    openai_config.resolve_openai_config.cache_clear()
    with pytest.raises(ValueError, match="Missing OPENAI_API_KEY"):
        openai_config.resolve_openai_config(get_secret=_secret_reader({"openai_model": "gpt-4o-mini"}))


def test_get_secret_model_does_not_return_api_key(monkeypatch):
    """_get_secret('openai_model') must never return the OPENAI_API_KEY value.

    This is a regression test for the bug where the _get_secret candidates list
    included OPENAI_API_KEY when looking for openai_model (because both contain
    the word 'openai'). When OPENAI_MODEL was unset, the function fell back to
    OPENAI_API_KEY and returned the API key string as the model name, causing
    OpenAI to reject the request with a 404 model_not_found error.
    """
    monkeypatch.delenv("openai_model", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-key")

    result = utils._get_secret("openai_model", openai_config.DEFAULT_OPENAI_MODEL)

    assert not result.startswith("sk-"), (
        f"_get_secret('openai_model') returned an API key value: {result!r}"
    )
    assert result == openai_config.DEFAULT_OPENAI_MODEL
