from __future__ import annotations

from src.config import get_secret


def _read_secret(name: str, aliases: tuple[str, ...] = ()) -> str:
    keys = (name, *aliases)
    for key in keys:
        value = str(get_secret(key, "") or "").strip()
        if value:
            return value
    return ""


def get_pexels_api_key() -> str:
    return _read_secret("PEXELS_API_KEY", aliases=("pexels_api_key",))


def get_pixabay_api_key() -> str:
    return _read_secret("PIXABAY_API_KEY", aliases=("pixabay_api_key",))


def broll_provider_status() -> dict[str, bool]:
    return {
        "pexels": bool(get_pexels_api_key()),
        "pixabay": bool(get_pixabay_api_key()),
    }
