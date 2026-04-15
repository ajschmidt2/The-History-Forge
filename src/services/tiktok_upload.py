"""
src/services/tiktok_upload.py

Uploads videos to TikTok via the Content Posting API v2 (file-upload path).

Required secrets (in .streamlit/secrets.toml or env):
    TIKTOK_ACCESS_TOKEN  — OAuth 2.0 access token
    TIKTOK_OPEN_ID       — TikTok user open_id (returned during OAuth)

To get credentials:
  1. Register a developer app at https://developers.tiktok.com
  2. Enable the "Content Posting API" product
  3. Request scopes: video.publish, video.upload
  4. Complete OAuth and obtain access_token + open_id
  5. Paste into secrets.toml:
       TIKTOK_ACCESS_TOKEN = "act.xxx..."
       TIKTOK_OPEN_ID = "..."

Token refresh:
    POST https://open.tiktokapis.com/v2/oauth/token/
    grant_type=refresh_token
    &client_key=...
    &refresh_token=...
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from src.config.secrets import get_secret

log = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"

# TikTok recommends chunk sizes between 5 MB and 64 MB
_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB

# Maximum polls when waiting for TikTok to process the upload
_MAX_STATUS_POLLS = 40
_STATUS_POLL_INTERVAL_S = 8

PRIVACY_OPTIONS = {
    "Public": "PUBLIC_TO_EVERYONE",
    "Friends only": "MUTUAL_FOLLOW_FRIENDS",
    "Private (self only)": "SELF_ONLY",
}


class TikTokUploadError(RuntimeError):
    """Raised for user-facing TikTok upload failures."""


@dataclass(slots=True)
class TikTokUploadResult:
    publish_id: str
    share_url: str | None = None


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _get_access_token() -> str | None:
    value = get_secret("TIKTOK_ACCESS_TOKEN") or get_secret("tiktok_access_token") or ""
    return value.strip() or None


def _get_open_id() -> str | None:
    value = get_secret("TIKTOK_OPEN_ID") or get_secret("tiktok_open_id") or ""
    return value.strip() or None


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_access_token()}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def tiktok_configured() -> bool:
    """Return True if TikTok credentials are present in secrets."""
    try:
        return bool(_get_access_token())
    except Exception:
        return False


def validate_tiktok_credentials() -> tuple[bool, str]:
    """Test TikTok credentials by fetching the user's display name.

    Returns (is_valid, message).
    """
    token = _get_access_token()
    if not token:
        return False, (
            "TikTok credentials not configured. "
            "Set TIKTOK_ACCESS_TOKEN in secrets.toml."
        )

    try:
        resp = requests.post(
            f"{TIKTOK_API_BASE}/user/info/",
            headers=_auth_headers(),
            json={"fields": ["display_name", "username", "avatar_url"]},
            timeout=15,
        )
        data = resp.json()
        if not resp.ok or data.get("error", {}).get("code", "ok") != "ok":
            err_msg = data.get("error", {}).get("message", resp.text[:200])
            return False, f"TikTok token validation failed: {err_msg}"

        user = data.get("data", {}).get("user", {})
        display_name = user.get("display_name") or user.get("username") or "unknown"
        return True, f"Connected as @{display_name}"
    except Exception as exc:  # noqa: BLE001
        return False, f"TikTok validation error: {exc}"


def query_creator_info() -> dict:
    """Fetch the creator's posting settings (privacy options, duet/stitch caps, etc.)."""
    resp = requests.post(
        f"{TIKTOK_API_BASE}/post/publish/creator_info/query/",
        headers=_auth_headers(),
        json={},
        timeout=15,
    )
    return resp.json()


# ---------------------------------------------------------------------------
# Core upload
# ---------------------------------------------------------------------------

def upload_video(
    video_path: str | Path,
    *,
    title: str,
    privacy_level: str = "SELF_ONLY",
    disable_comment: bool = False,
    disable_duet: bool = False,
    disable_stitch: bool = False,
    video_cover_timestamp_ms: int = 1000,
) -> TikTokUploadResult:
    """Upload a video to TikTok using the direct file-upload (chunked) path.

    Args:
        video_path:              Local path to the MP4 file.
        title:                   Post title / caption (max 2200 chars).
        privacy_level:           One of PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, SELF_ONLY.
        disable_comment:         Disable comments on the post.
        disable_duet:            Disable Duet.
        disable_stitch:          Disable Stitch.
        video_cover_timestamp_ms: Millisecond timestamp for the cover frame.

    Returns:
        TikTokUploadResult with publish_id and share_url.

    Raises:
        TikTokUploadError on any failure.
    """
    if not tiktok_configured():
        raise TikTokUploadError(
            "TikTok credentials not configured. "
            "Set TIKTOK_ACCESS_TOKEN in secrets.toml."
        )

    local_path = Path(video_path).expanduser()
    if not local_path.exists():
        raise TikTokUploadError(f"Video file not found: {local_path}")

    video_size = local_path.stat().st_size
    chunk_size = min(_CHUNK_SIZE, video_size)
    total_chunks = math.ceil(video_size / chunk_size)

    # Step 1 — Initialize the upload
    log.info(
        "tiktok: initializing upload file=%s size=%d chunks=%d",
        local_path.name, video_size, total_chunks,
    )
    init_payload = {
        "post_info": {
            "title": title.strip()[:2200],
            "privacy_level": privacy_level,
            "disable_duet": disable_duet,
            "disable_comment": disable_comment,
            "disable_stitch": disable_stitch,
            "video_cover_timestamp_ms": video_cover_timestamp_ms,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    }
    init_resp = requests.post(
        f"{TIKTOK_API_BASE}/post/publish/video/init/",
        headers=_auth_headers(),
        json=init_payload,
        timeout=30,
    )
    init_data = init_resp.json()
    _check_tiktok_error(init_data, "upload initialization")

    publish_id = str(init_data.get("data", {}).get("publish_id", "")).strip()
    upload_url = str(init_data.get("data", {}).get("upload_url", "")).strip()

    if not publish_id or not upload_url:
        raise TikTokUploadError(
            f"TikTok did not return publish_id/upload_url: {init_data}"
        )
    log.info("tiktok: publish_id=%s upload_url obtained", publish_id)

    # Step 2 — Upload chunks
    with local_path.open("rb") as fh:
        for chunk_idx in range(total_chunks):
            chunk_data = fh.read(chunk_size)
            actual_chunk_size = len(chunk_data)
            byte_start = chunk_idx * chunk_size
            byte_end = byte_start + actual_chunk_size - 1

            log.info(
                "tiktok: uploading chunk %d/%d bytes=%d-%d",
                chunk_idx + 1, total_chunks, byte_start, byte_end,
            )
            chunk_resp = requests.put(
                upload_url,
                data=chunk_data,
                headers={
                    "Content-Range": f"bytes {byte_start}-{byte_end}/{video_size}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(actual_chunk_size),
                },
                timeout=300,
            )
            if not chunk_resp.ok:
                raise TikTokUploadError(
                    f"Chunk {chunk_idx + 1}/{total_chunks} upload failed "
                    f"({chunk_resp.status_code}): {chunk_resp.text[:200]}"
                )

    log.info("tiktok: all chunks uploaded, polling status...")

    # Step 3 — Poll publish status
    share_url: str | None = None
    for attempt in range(_MAX_STATUS_POLLS):
        time.sleep(_STATUS_POLL_INTERVAL_S)
        status_resp = requests.post(
            f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
            headers=_auth_headers(),
            json={"publish_id": publish_id},
            timeout=15,
        )
        status_data = status_resp.json()
        _check_tiktok_error(status_data, "status poll")

        status = str(
            status_data.get("data", {}).get("status", "")
        ).upper()
        log.info(
            "tiktok: publish_id=%s poll %d/%d status=%s",
            publish_id, attempt + 1, _MAX_STATUS_POLLS, status,
        )

        if status in {"PUBLISH_COMPLETE", "PUBLISHED"}:
            share_url = status_data.get("data", {}).get("share_url")
            log.info("tiktok: published share_url=%s", share_url)
            break

        if status in {"FAILED", "PUBLISH_FAILED"}:
            fail_reason = status_data.get("data", {}).get("fail_reason", "unknown")
            raise TikTokUploadError(
                f"TikTok publish failed: status={status} reason={fail_reason}"
            )
    else:
        raise TikTokUploadError(
            f"TikTok publish did not complete within "
            f"{_MAX_STATUS_POLLS * _STATUS_POLL_INTERVAL_S}s (publish_id={publish_id})."
        )

    return TikTokUploadResult(publish_id=publish_id, share_url=share_url)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_tiktok_error(data: dict, context: str) -> None:
    """Raise TikTokUploadError if the API response contains an error."""
    err = data.get("error", {})
    if err and err.get("code", "ok").lower() != "ok":
        raise TikTokUploadError(
            f"TikTok API error during {context}: "
            f"code={err.get('code')} message={err.get('message', str(err)[:200])}"
        )
