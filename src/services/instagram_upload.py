"""
src/services/instagram_upload.py

Uploads Reels to Instagram via the Meta Graph API (Content Publishing API).

Instagram requires the video to be accessible via a **public URL**.
This module checks the project payload for a Supabase-hosted public URL first;
if none exists, it temporarily uploads the local file to Supabase storage and
uses that URL.

Required secrets (in .streamlit/secrets.toml or env):
    INSTAGRAM_USER_ID       — Instagram Professional/Creator account user ID
    INSTAGRAM_ACCESS_TOKEN  — Long-lived user access token with scopes:
                              instagram_content_publish, pages_read_engagement

To get credentials:
  1. Create a Meta Developer app at https://developers.facebook.com
  2. Add the Instagram Graph API product
  3. Connect your Instagram Professional account
  4. Generate a User Access Token with the required permissions
  5. Exchange for a long-lived token (60-day expiry, renewable):
     GET https://graph.facebook.com/v19.0/oauth/access_token
       ?grant_type=fb_exchange_token
       &client_id={app_id}&client_secret={app_secret}
       &fb_exchange_token={short_lived_token}
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from src.config.secrets import get_secret, safe_secret, safe_str
from src.video.utils import resolve_ffmpeg_exe

log = logging.getLogger(__name__)

GRAPH_API_VERSION = "v22.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

_TOKEN_CACHE_PATH = Path("data/instagram_token.json")

PRIVACY_OPTIONS: dict[str, str] = {}  # Instagram Reels don't have an API-level privacy toggle

# Maximum polls when waiting for Instagram to process the video container
_MAX_CONTAINER_POLLS = 30
_CONTAINER_POLL_INTERVAL_S = 10


class InstagramUploadError(RuntimeError):
    """Raised for user-facing Instagram upload failures."""


@dataclass(slots=True)
class InstagramUploadResult:
    media_id: str
    permalink: str | None = None


@dataclass(slots=True)
class InstagramTokenHealth:
    configured: bool
    valid: bool
    can_publish: bool
    refresh_supported: bool
    expires_at: datetime | None
    seconds_remaining: int | None
    app_id: str
    scopes: list[str]
    message: str


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _get_user_id() -> str:
    user_id = safe_secret("INSTAGRAM_USER_ID", "instagram_user_id", default="")
    log.debug(
        "instagram: resolved user id (exists=%s, length=%d)",
        bool(user_id),
        len(user_id),
    )
    return user_id


def _load_cached_token() -> str:
    """Return a previously refreshed token if it hasn't expired yet."""
    try:
        data = json.loads(_TOKEN_CACHE_PATH.read_text())
        token = safe_str(data.get("access_token", ""))
        expires_at = datetime.fromisoformat(data.get("expires_at", ""))
        if token and expires_at > datetime.now(timezone.utc):
            return token
    except Exception:
        pass
    return ""


def save_cached_token(token: str, expires_in: int) -> None:
    """Persist a refreshed token to disk so it survives restarts."""
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    _TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_CACHE_PATH.write_text(json.dumps({"access_token": token, "expires_at": expires_at}, indent=2))
    log.info("instagram: token cached, expires %s", expires_at)


def _get_access_token() -> str:
    cached = _load_cached_token()
    if cached:
        log.debug(
            "instagram: using cached access token (exists=%s, length=%d)",
            True,
            len(cached),
        )
        return cached
    token = safe_secret("INSTAGRAM_ACCESS_TOKEN", "instagram_access_token", default="")
    log.debug(
        "instagram: resolved access token (exists=%s, length=%d)",
        bool(token),
        len(token),
    )
    return token


def inspect_access_token(
    *,
    token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> dict[str, Any]:
    """Inspect the current Instagram/Meta token via the Graph debug_token endpoint."""
    access_token = str(token or _get_access_token() or "").strip()
    if not access_token:
        raise InstagramUploadError("No INSTAGRAM_ACCESS_TOKEN configured.")

    _app_id = str(app_id or get_secret("META_APP_ID") or get_secret("meta_app_id") or "").strip()
    _app_secret = str(app_secret or get_secret("META_APP_SECRET") or get_secret("meta_app_secret") or "").strip()
    if not _app_id or not _app_secret:
        raise InstagramUploadError("META_APP_ID and META_APP_SECRET are required to inspect the Instagram token.")

    app_access_token = f"{_app_id}|{_app_secret}"
    resp = requests.get(
        f"{GRAPH_BASE}/debug_token",
        params={"input_token": access_token, "access_token": app_access_token},
        timeout=15,
    )
    data = resp.json()
    if not resp.ok or "error" in data:
        err = data.get("error", {}).get("message", resp.text[:200])
        raise InstagramUploadError(
            "Instagram token inspection failed. META_APP_ID / META_APP_SECRET may not match the app "
            f"that issued this token. Details: {err}"
        )

    token_data = data.get("data")
    if not isinstance(token_data, dict):
        raise InstagramUploadError("Instagram token inspection returned an unexpected payload.")
    return token_data


def should_refresh_access_token(
    *,
    window_days: int = 7,
    token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> tuple[bool, int | None]:
    """Return (should_refresh, seconds_remaining_or_none)."""
    token_data = inspect_access_token(token=token, app_id=app_id, app_secret=app_secret)
    expires_at = token_data.get("expires_at")
    if not token_data.get("is_valid", False):
        return True, None
    if not isinstance(expires_at, int) or expires_at <= 0:
        return True, None

    seconds_remaining = int(expires_at - int(time.time()))
    refresh_window = max(1, int(window_days)) * 86400
    return seconds_remaining <= refresh_window, seconds_remaining


def get_token_health(*, refresh_window_days: int = 7) -> InstagramTokenHealth:
    user_id = _get_user_id()
    token = _get_access_token()
    app_id = str(get_secret("META_APP_ID") or get_secret("meta_app_id") or "").strip()

    if not user_id or not token:
        return InstagramTokenHealth(
            configured=False,
            valid=False,
            can_publish=False,
            refresh_supported=False,
            expires_at=None,
            seconds_remaining=None,
            app_id=app_id,
            scopes=[],
            message="Instagram credentials are not configured.",
        )

    try:
        token_data = inspect_access_token(token=token)
        scopes = [str(scope).strip() for scope in (token_data.get("scopes") or []) if str(scope).strip()]
        expires_at_raw = token_data.get("expires_at")
        expires_at: datetime | None = None
        seconds_remaining: int | None = None
        if isinstance(expires_at_raw, int) and expires_at_raw > 0:
            expires_at = datetime.fromtimestamp(expires_at_raw, tz=timezone.utc)
            seconds_remaining = int(expires_at_raw - int(time.time()))

        valid = bool(token_data.get("is_valid", False))
        can_publish = False
        if valid:
            can_publish, publish_msg = validate_instagram_credentials()
        else:
            publish_msg = "Instagram token is not valid."

        refresh_supported = bool(valid and app_id)
        if valid and seconds_remaining is not None:
            threshold = max(1, int(refresh_window_days)) * 86400
            if seconds_remaining <= threshold:
                message = f"{publish_msg} Token should be refreshed soon."
            else:
                message = publish_msg
        else:
            message = publish_msg

        return InstagramTokenHealth(
            configured=True,
            valid=valid,
            can_publish=can_publish,
            refresh_supported=refresh_supported,
            expires_at=expires_at,
            seconds_remaining=seconds_remaining,
            app_id=app_id,
            scopes=scopes,
            message=message,
        )
    except Exception as exc:  # noqa: BLE001
        return InstagramTokenHealth(
            configured=True,
            valid=False,
            can_publish=False,
            refresh_supported=False,
            expires_at=None,
            seconds_remaining=None,
            app_id=app_id,
            scopes=[],
            message=str(exc),
        )


def instagram_configured() -> bool:
    """Return True if Instagram credentials are present in secrets."""
    try:
        user_id = _get_user_id()
        token = _get_access_token()
        log.debug(
            "instagram: configured check INSTAGRAM_USER_ID exists=%s len=%d; "
            "INSTAGRAM_ACCESS_TOKEN exists=%s len=%d",
            bool(user_id),
            len(user_id),
            bool(token),
            len(token),
        )
        return bool(user_id and token)
    except Exception as exc:  # noqa: BLE001
        log.warning("instagram: configuration check failed: %s", exc)
        return False


def validate_instagram_credentials() -> tuple[bool, str]:
    """Test Instagram credentials by fetching the account's username.

    Returns (is_valid, message).
    """
    user_id = _get_user_id()
    token = _get_access_token()

    if not user_id or not token:
        return False, (
            "Instagram credentials not configured. "
            "Set INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN in secrets.toml."
        )

    try:
        token_resp = requests.get(
            f"{GRAPH_BASE}/me",
            params={"fields": "id", "access_token": token},
            timeout=15,
        )
        token_data = token_resp.json()
        if not token_resp.ok or "error" in token_data:
            err = token_data.get("error", {}).get("message", token_resp.text[:200])
            return False, f"Instagram token validation failed: {err}"

        user_resp = requests.get(
            f"{GRAPH_BASE}/{user_id}",
            params={"fields": "id", "access_token": token},
            timeout=15,
        )
        user_data = user_resp.json()
        if not user_resp.ok or "error" in user_data:
            return (
                False,
                "Instagram token is valid, but INSTAGRAM_USER_ID is not a publishable Instagram account ID. "
                "Use the Instagram business account ID that can open the /media edge.",
            )

        media_resp = requests.get(
            f"{GRAPH_BASE}/{user_id}/media",
            params={"access_token": token},
            timeout=15,
        )
        media_data = media_resp.json()
        if media_resp.ok and "error" not in media_data and isinstance(media_data.get("data"), list):
            return True, "Instagram token and publish target are configured."

        accounts_resp = requests.get(
            f"{GRAPH_BASE}/me/accounts",
            params={"access_token": token},
            timeout=15,
        )
        accounts_data = accounts_resp.json()
        if accounts_resp.ok and isinstance(accounts_data.get("data"), list) and not accounts_data.get("data"):
            return (
                False,
                "Instagram token is valid, but it does not currently expose any Facebook Pages and the media edge "
                "check did not confirm the publish target.",
            )

        err = media_data.get("error", {}).get("message", media_resp.text[:200]) if isinstance(media_data, dict) else media_resp.text[:200]
        return False, f"Instagram publish target validation failed: {err}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Instagram validation error: {exc}"


def refresh_access_token(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> tuple[str, int]:
    """Exchange current long-lived token for a fresh one (resets 60-day expiry).

    Returns (new_token, expires_in_seconds).
    Raises InstagramUploadError on failure.
    """
    token = _get_access_token()
    if not token:
        raise InstagramUploadError("No INSTAGRAM_ACCESS_TOKEN configured.")

    _app_id = app_id or get_secret("META_APP_ID") or get_secret("meta_app_id")
    _app_secret = app_secret or get_secret("META_APP_SECRET") or get_secret("meta_app_secret")

    if not _app_id or not _app_secret:
        raise InstagramUploadError(
            "META_APP_ID and META_APP_SECRET are required to refresh the access token."
        )

    try:
        should_refresh, seconds_remaining = should_refresh_access_token(
            token=token,
            app_id=_app_id,
            app_secret=_app_secret,
        )
        if not should_refresh:
            return token, int(seconds_remaining or 0)
    except InstagramUploadError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("instagram: token preflight inspection failed before refresh: %s", exc)

    resp = requests.get(
        f"{GRAPH_BASE}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": _app_id,
            "client_secret": _app_secret,
            "fb_exchange_token": token,
        },
        timeout=15,
    )
    data = resp.json()
    if not resp.ok or "error" in data:
        err = data.get("error", {}).get("message", resp.text[:200])
        raise InstagramUploadError(
            "Token refresh failed. META_APP_ID / META_APP_SECRET may not match the app that issued this token, "
            f"or this token type may not support refresh through this endpoint. Details: {err}"
        )

    return data["access_token"], int(data.get("expires_in", 0))


# ---------------------------------------------------------------------------
# Public URL resolution
# ---------------------------------------------------------------------------


def _prepare_instagram_reel_video(
    video_path: str | Path,
    *,
    project_id: str | None = None,
) -> Path:
    """Re-encode the source video to a conservative Instagram-safe MP4."""
    source_path = Path(video_path).expanduser()
    if not source_path.exists():
        raise InstagramUploadError(f"Video file not found: {source_path}")

    try:
        ffmpeg_exe = resolve_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise InstagramUploadError(f"FFmpeg is required to prepare Instagram uploads: {exc}") from exc

    temp_root = Path("data") / "instagram_uploads"
    temp_root.mkdir(parents=True, exist_ok=True)
    slug = project_id or source_path.stem
    prepared_path = temp_root / f"{slug}_instagram_safe.mp4"
    tmp_output = prepared_path.with_name(f"{prepared_path.stem}_tmp.mp4")
    if tmp_output.exists():
        tmp_output.unlink()

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(source_path),
        "-vf",
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=30,format=yuv420p",
        "-c:v",
        "libx264",
        "-profile:v",
        "main",
        "-level:v",
        "4.0",
        "-preset",
        "medium",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(tmp_output),
    ]
    log.info("instagram: preparing upload-safe reel from %s", source_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not tmp_output.exists():
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise InstagramUploadError(f"Failed to prepare Instagram-safe video: {detail}")
    if prepared_path.exists():
        prepared_path.unlink()
    tmp_output.replace(prepared_path)
    return prepared_path

def _ensure_public_url(
    video_path: str | Path,
    *,
    project_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    """Return a publicly accessible URL for the video file.

    Tries in order:
      1. payload["generated_video_public_url"] if it points to this video
      2. Upload the local file to Supabase storage and return the public URL
    """
    # 1. Use existing Supabase public URL if available
    if payload:
        existing_url = str(payload.get("generated_video_public_url", "") or "").strip()
        if existing_url.startswith("http"):
            log.info("instagram: reusing existing public URL %s", existing_url)
            return existing_url

    # 2. Upload temporarily to Supabase
    local_path = Path(video_path).expanduser()
    if not local_path.exists():
        raise InstagramUploadError(f"Video file not found: {local_path}")

    try:
        from src.config.secrets import get_secret as _gs
        supabase_url = _gs("SUPABASE_URL")
        supabase_key = _gs("SUPABASE_SERVICE_ROLE_KEY") or _gs("SUPABASE_KEY")
        bucket = _gs("SUPABASE_VIDEOS_BUCKET") or "generated-videos"

        if not supabase_url or not supabase_key:
            raise InstagramUploadError(
                "No public URL available and Supabase is not configured. "
                "Instagram requires a publicly accessible video URL. "
                "Run the daily job first (which uploads to Supabase), or configure Supabase."
            )

        slug = project_id or local_path.stem
        object_path = f"{slug}/instagram_reel_{int(time.time())}.mp4"
        upload_endpoint = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"
        headers = {
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "video/mp4",
        }
        log.info("instagram: uploading %s to Supabase for public URL", local_path)
        with local_path.open("rb") as fh:
            resp = requests.post(upload_endpoint, data=fh, headers=headers, timeout=300)

        if not resp.ok:
            raise InstagramUploadError(
                f"Supabase upload failed ({resp.status_code}): {resp.text[:200]}"
            )

        public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}"
        log.info("instagram: Supabase public URL = %s", public_url)
        return public_url

    except InstagramUploadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InstagramUploadError(
            f"Could not obtain a public URL for the video: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Core upload
# ---------------------------------------------------------------------------

def upload_reel(
    video_path: str | Path,
    *,
    caption: str = "",
    cover_timestamp_ms: int = 1000,
    project_id: str | None = None,
    payload: dict[str, Any] | None = None,
    share_to_feed: bool = True,
) -> InstagramUploadResult:
    """Upload a video as an Instagram Reel.

    Args:
        video_path:         Local path to the MP4 file.
        caption:            Caption text (include hashtags here).
        cover_timestamp_ms: Millisecond timestamp for the cover frame.
        project_id:         Used to find/name the Supabase upload path.
        payload:            Project payload dict (for existing public URL lookup).
        share_to_feed:      Whether to share the Reel to the main feed.

    Returns:
        InstagramUploadResult with media_id and permalink.

    Raises:
        InstagramUploadError on any failure.
    """
    if not instagram_configured():
        raise InstagramUploadError(
            "Instagram credentials not configured. "
            "Set INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN in secrets.toml."
        )

    user_id = _get_user_id()
    token = _get_access_token()

    # Step 1 — Normalize to a conservative Instagram-safe MP4, then upload it
    prepared_video_path = _prepare_instagram_reel_video(video_path, project_id=project_id)
    video_url = _ensure_public_url(prepared_video_path, project_id=project_id, payload=None)

    # Step 2 — Create media container
    log.info("instagram: creating Reel container for user %s", user_id)
    container_payload: dict[str, Any] = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption.strip(),
        "share_to_feed": share_to_feed,
        "access_token": token,
    }
    if cover_timestamp_ms:
        container_payload["video_cover_timestamp_ms"] = cover_timestamp_ms

    container_resp = requests.post(
        f"{GRAPH_BASE}/{user_id}/media",
        json=container_payload,
        timeout=60,
    )
    container_data = container_resp.json()
    if not container_resp.ok or "error" in container_data:
        err = container_data.get("error", {}).get("message", container_resp.text[:300])
        raise InstagramUploadError(f"Failed to create Instagram media container: {err}")

    creation_id = str(container_data.get("id", "")).strip()
    if not creation_id:
        raise InstagramUploadError(
            f"No container ID returned by Instagram: {container_data}"
        )
    log.info("instagram: container created id=%s", creation_id)

    # Step 3 — Poll until container is ready
    log.info("instagram: polling container status...")
    for attempt in range(_MAX_CONTAINER_POLLS):
        time.sleep(_CONTAINER_POLL_INTERVAL_S)
        status_resp = requests.get(
            f"{GRAPH_BASE}/{creation_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=15,
        )
        status_data = status_resp.json()
        status_code = str(status_data.get("status_code", "")).upper()
        log.info(
            "instagram: container %s poll %d/%d status=%s",
            creation_id, attempt + 1, _MAX_CONTAINER_POLLS, status_code,
        )
        if status_code == "FINISHED":
            break
        if status_code in {"ERROR", "EXPIRED"}:
            raise InstagramUploadError(
                f"Instagram media container processing failed: status={status_code} — {status_data}"
            )
    else:
        raise InstagramUploadError(
            f"Instagram media container did not finish processing within "
            f"{_MAX_CONTAINER_POLLS * _CONTAINER_POLL_INTERVAL_S}s."
        )

    # Step 4 — Publish
    log.info("instagram: publishing Reel creation_id=%s", creation_id)
    publish_resp = requests.post(
        f"{GRAPH_BASE}/{user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=30,
    )
    publish_data = publish_resp.json()
    if not publish_resp.ok or "error" in publish_data:
        err = publish_data.get("error", {}).get("message", publish_resp.text[:300])
        raise InstagramUploadError(f"Instagram publish failed: {err}")

    media_id = str(publish_data.get("id", "")).strip()
    if not media_id:
        raise InstagramUploadError(f"No media ID returned after publish: {publish_data}")

    # Step 5 — Fetch permalink
    permalink = None
    try:
        link_resp = requests.get(
            f"{GRAPH_BASE}/{media_id}",
            params={"fields": "permalink", "access_token": token},
            timeout=10,
        )
        permalink = link_resp.json().get("permalink")
    except Exception:  # noqa: BLE001
        pass

    log.info("instagram: Reel published media_id=%s permalink=%s", media_id, permalink)
    return InstagramUploadResult(media_id=media_id, permalink=permalink)
