from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REQUIRED_TREND_TABLES: tuple[str, ...] = (
    "trend_scan_runs",
    "trend_topic_results",
    "saved_topic_candidates",
)


@dataclass(frozen=True)
class TrendPersistenceValidationResult:
    is_ready: bool
    missing_tables: tuple[str, ...] = ()
    schema_errors: tuple[str, ...] = ()

    @property
    def admin_message(self) -> str:
        parts: list[str] = [
            "Trend Intelligence persistence is not configured in Supabase.",
        ]
        if self.missing_tables:
            parts.append("Missing tables: " + ", ".join(self.missing_tables) + ".")
        if self.schema_errors:
            parts.append("Schema errors: " + " | ".join(self.schema_errors) + ".")
        parts.append(
            "Run the Trend Intelligence migrations (for example, "
            "`supabase/migrations/20260325000000_add_trend_intelligence_tables.sql` and "
            "`supabase/migrations/20260325010000_trend_intelligence_persistence_v2.sql`) and reload the app."
        )
        return " ".join(parts)


def looks_like_schema_error(exc: Exception) -> bool:
    text = _error_blob(exc).lower()
    markers = (
        "42p01",
        "3f000",
        "42703",
        "pgrst",
        "relation",
        "does not exist",
        "schema cache",
        "undefined table",
        "undefined column",
    )
    return any(marker in text for marker in markers)


def _error_blob(exc: Exception) -> str:
    pieces: list[str] = [str(exc)]
    for attr in ("code", "message", "details", "hint"):
        value: Any = getattr(exc, attr, None)
        if value:
            pieces.append(str(value))
    return " | ".join(pieces)
