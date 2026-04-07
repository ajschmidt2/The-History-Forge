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
