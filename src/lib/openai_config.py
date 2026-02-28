import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

OPENAI_MODEL_OPTIONS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str


@lru_cache(maxsize=1)
def resolve_openai_config(get_secret: Callable[[str, str], str] | None = None) -> OpenAIConfig:
    """Resolve and validate OpenAI credentials/model from secrets or env.

    `get_secret` should have the same signature as utils._get_secret.
    """

    reader = get_secret or (lambda name, default="": default)

    api_key = reader("openai_api_key", "").strip()
    model = reader("openai_model", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL

    if not api_key:
        raise ValueError("Missing OPENAI_API_KEY")

    lowered_model = model.lower()
    if "sk-" in lowered_model:
        raise ValueError(
            "Misconfiguration: OPENAI_MODEL is an API key. Set OPENAI_MODEL to a model id like gpt-4o-mini."
        )

    _logger.info(
        "OpenAI configuration loaded (model=%s, api_key_prefix=%s***).",
        model,
        api_key[:6],
    )

    return OpenAIConfig(api_key=api_key, model=model)
