from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REQUIRED_TREND_TABLES: tuple[str, ...] = (
    "trend_scan_runs",
    "trend_topic_results",
    "saved_topic_candidates",
)

ADMIN_STATUS_MESSAGES: dict[str, str] = {
    "schema_cache": (
        "Trend Intelligence tables exist, but the Supabase API schema cache may be stale. "
        "Run NOTIFY pgrst, 'reload schema'; in Supabase SQL Editor, then reload the app."
    ),
    "missing_tables": (
        "Trend Intelligence persistence is not configured yet. "
        "Apply the Trend Intelligence Supabase migrations, then reload the app."
    ),
    "permission_error": (
        "Trend Intelligence tables exist, but current client credentials do not have access. "
        "Review RLS policies for these tables."
    ),
    "connection_error": (
        "Trend Intelligence setup check could not be completed due to a Supabase connection or API error."
    ),
}


@dataclass(frozen=True)
class TrendPersistenceValidationResult:
    is_ready: bool
    status: str = "ready"
    missing_tables: tuple[str, ...] = ()
    details: dict[str, Any] | None = None

    @property
    def admin_message(self) -> str:
        if self.is_ready:
            return "Trend Intelligence persistence is configured and ready."
        return ADMIN_STATUS_MESSAGES.get(self.status, ADMIN_STATUS_MESSAGES["connection_error"])


def classify_trend_setup_error(error_text: str) -> str:
    text = str(error_text or "").lower()
    if "information_schema.columns" in text or "public.information_schema.columns" in text:
        return "schema_cache"
    if ("relation" in text and "does not exist" in text) or "could not find the table" in text:
        return "missing_tables"

    permission_markers = (
        "permission denied",
        "not allowed",
        "not authorized",
        "forbidden",
        "rls",
        "row-level security",
        "42501",
        "jwt",
        "insufficient privilege",
    )
    if any(marker in text for marker in permission_markers):
        return "permission_error"

    return "connection_error"


def _response_error_text(resp: Any) -> str:
    raw_error = getattr(resp, "error", None)
    if raw_error:
        return str(raw_error)

    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        fragments = [str(data.get("code", "")), str(data.get("message", "")), str(data.get("hint", ""))]
        return " | ".join(fragment for fragment in fragments if fragment and fragment != "None")
    return ""


def check_trend_intelligence_setup(supabase) -> dict[str, Any]:
    if supabase is None:
        return {
            "ok": False,
            "status": "connection_error",
            "details": {"error": "Supabase client is unavailable."},
        }

    table_errors: dict[str, str] = {}
    for table_name in REQUIRED_TREND_TABLES:
        try:
            resp = supabase.table(table_name).select("id").limit(1).execute()
            response_error = _response_error_text(resp)
            if response_error:
                table_errors[table_name] = response_error
        except Exception as exc:  # noqa: BLE001
            table_errors[table_name] = str(exc)

    if not table_errors:
        return {
            "ok": True,
            "status": "ready",
            "details": {"tables_checked": list(REQUIRED_TREND_TABLES)},
        }

    statuses = [classify_trend_setup_error(msg) for msg in table_errors.values()]
    if "schema_cache" in statuses:
        status = "schema_cache"
    elif "missing_tables" in statuses:
        status = "missing_tables"
    elif "permission_error" in statuses:
        status = "permission_error"
    else:
        status = "connection_error"

    missing_tables = tuple(
        table for table, msg in table_errors.items() if classify_trend_setup_error(msg) == "missing_tables"
    )

    return {
        "ok": False,
        "status": status,
        "details": {
            "table_errors": table_errors,
            "missing_tables": missing_tables,
        },
    }


def build_trend_persistence_admin_message(*, status: str, details: dict[str, Any] | None = None) -> str:
    _ = details
    return ADMIN_STATUS_MESSAGES.get(status, ADMIN_STATUS_MESSAGES["connection_error"])


def looks_like_schema_error(exc: Exception) -> bool:
    status = classify_trend_setup_error(_error_blob(exc))
    return status in {"schema_cache", "missing_tables", "permission_error"}


def _error_blob(exc: Exception) -> str:
    pieces: list[str] = [str(exc)]
    for attr in ("code", "message", "details", "hint"):
        value: Any = getattr(exc, attr, None)
        if value:
            pieces.append(str(value))
    return " | ".join(pieces)
