"""Diagnostic CLI for validating Sora access with the current API key.

Usage:
  python scripts/sora_health_check.py
"""
from __future__ import annotations

import sys

from src.ai_video_generation import sora_diagnostic_check


def main() -> int:
    try:
        ok, message = sora_diagnostic_check()
    except Exception as exc:  # noqa: BLE001
        print(f"Sora diagnostic failed: {exc}")
        return 1

    print(message)
    if not ok:
        print(
            "Hint: create a NEW API key from the exact org/project where Sora is enabled, "
            "set OPENAI_API_KEY/openai_api_key, and retry."
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
