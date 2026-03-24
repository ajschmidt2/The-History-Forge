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

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.config.secrets import get_secret

log = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

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


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _get_user_id() -> str:
    return (
        get_secret("INSTAGRAM_USER_ID")
        or get_secret("instagram_user_id")
    ).strip()


def _get_access_token() -> str:
    return (
        get_secret("INSTAGRAM_ACCESS_TOKEN")
        or get_secret("instagram_access_token")
    ).strip()


def instagram_configured() -> bool:
    """Return True if Instagram credentials are present in secrets."""
    return bool(_get_user_id() and _get_access_token())


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
        resp = requests.get(
            f"{GRAPH_BASE}/me",
            params={"fields": "id", "access_token": token},
            timeout=15,
        )
        data = resp.json()
        if not resp.ok or "error" in data:
            err = data.get("error", {}).get("message", resp.text[:200])
            return False, f"Instagram token validation failed: {err}"

        return True, "Connected as @the_history_crossroads"
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
        raise InstagramUploadError(f"Token refresh failed: {err}")

    return data["access_token"], int(data.get("expires_in", 0))


# ---------------------------------------------------------------------------
# Public URL resolution
# ---------------------------------------------------------------------------

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

    # Step 1 — Resolve public URL
    video_url = _ensure_public_url(video_path, project_id=project_id, payload=payload)

    # Step 2 — Create media container
    log.info("instagram: creating Reel container for user %s", user_id)
    container_resp = requests.post(
        f"{GRAPH_BASE}/{user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption.strip(),
            "share_to_feed": "true" if share_to_feed else "false",
            "cover_url": "",
            "video_cover_timestamp_ms": cover_timestamp_ms,
            "access_token": token,
        },
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
