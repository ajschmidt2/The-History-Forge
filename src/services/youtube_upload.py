from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.config.secrets import get_secret
from src.services.storage_resolver import resolve_upload_file

log = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
YOUTUBE_RENDER_BUCKET = "history-forge-videos"
YOUTUBE_THUMBNAIL_BUCKET = "history-forge-images"


class YouTubeUploadError(RuntimeError):
    """Raised when YouTube auth or upload operations fail in a user-facing way."""


@dataclass(slots=True)
class YouTubeUploadResult:
    video_id: str
    response: dict[str, Any]
    thumbnail_response: dict[str, Any] | None = None


@dataclass(slots=True)
class YouTubeAuthConfig:
    client_secrets_file: Path
    token_file: Path



def _resolve_auth_config(
    *,
    client_secrets_file: str | Path | None = None,
    token_file: str | Path | None = None,
) -> YouTubeAuthConfig:
    default_client_secrets = get_secret("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
    default_token_file = get_secret("YOUTUBE_TOKEN_FILE", "token.json")

    resolved_client_secrets = Path(client_secrets_file or default_client_secrets).expanduser()
    resolved_token_file = Path(token_file or default_token_file).expanduser()
    return YouTubeAuthConfig(client_secrets_file=resolved_client_secrets, token_file=resolved_token_file)



def _save_credentials(credentials: Credentials, token_file: Path) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")



def _load_credentials_from_token_file(token_file: Path) -> Credentials | None:
    if not token_file.exists():
        return None
    return Credentials.from_authorized_user_file(str(token_file), scopes=[YOUTUBE_UPLOAD_SCOPE])



def _refresh_if_needed(credentials: Credentials, token_file: Path) -> Credentials:
    if credentials.valid:
        return credentials

    if credentials.expired and credentials.refresh_token:
        log.info("Refreshing expired YouTube OAuth token from %s", token_file)
        credentials.refresh(Request())
        _save_credentials(credentials, token_file)
        return credentials

    raise YouTubeUploadError(
        "YouTube OAuth token is missing/invalid and could not be refreshed. "
        "Run OAuth sign-in again to create a fresh token."
    )



def _run_oauth_flow(client_secrets_file: Path, token_file: Path) -> Credentials:
    if not client_secrets_file.exists():
        raise YouTubeUploadError(
            "YouTube OAuth client secrets file was not found. "
            f"Expected path: {client_secrets_file}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_file), [YOUTUBE_UPLOAD_SCOPE])
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=False,
        access_type="offline",
        prompt="consent",
    )
    _save_credentials(credentials, token_file)
    return credentials



def validate_publish_at(publish_at: str | None) -> str | None:
    if not publish_at:
        return None

    raw = publish_at.strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise YouTubeUploadError(
            "publish_at must be an ISO-8601 timestamp (example: 2026-03-14T18:30:00Z)."
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    return parsed.isoformat().replace("+00:00", "Z")



def validate_youtube_credentials(
    *,
    client_secrets_file: str | Path | None = None,
    token_file: str | Path | None = None,
) -> tuple[bool, str]:
    config = _resolve_auth_config(client_secrets_file=client_secrets_file, token_file=token_file)

    if config.token_file.exists():
        try:
            creds = _load_credentials_from_token_file(config.token_file)
            if creds is None:
                return False, f"Token file is unreadable: {config.token_file}"
            _refresh_if_needed(creds, config.token_file)
            return True, f"Token is valid: {config.token_file}"
        except Exception as exc:  # noqa: BLE001
            return False, f"Token validation failed: {exc}"

    if config.client_secrets_file.exists():
        return False, (
            f"Token file not found at {config.token_file}. "
            "Client secrets are present; run OAuth to generate token.json."
        )

    return False, (
        "No YouTube credentials found. Configure YOUTUBE_CLIENT_SECRETS_FILE and "
        "YOUTUBE_TOKEN_FILE (or place client_secrets.json/token.json in repo root)."
    )



def get_youtube_service(
    *,
    client_secrets_file: str | Path | None = None,
    token_file: str | Path | None = None,
    allow_local_oauth: bool = False,
) -> Resource:
    config = _resolve_auth_config(client_secrets_file=client_secrets_file, token_file=token_file)

    credentials = _load_credentials_from_token_file(config.token_file)
    if credentials is not None:
        credentials = _refresh_if_needed(credentials, config.token_file)
    elif allow_local_oauth:
        credentials = _run_oauth_flow(config.client_secrets_file, config.token_file)
    else:
        raise YouTubeUploadError(
            "No valid YouTube OAuth token found. Add token.json or enable local OAuth flow."
        )

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=credentials)



def set_thumbnail(
    *,
    youtube: Resource,
    video_id: str,
    thumbnail_path: str | Path,
) -> dict[str, Any]:
    thumb = Path(thumbnail_path).expanduser()
    if not thumb.exists() or not thumb.is_file():
        raise YouTubeUploadError(f"Thumbnail file not found: {thumb}")

    request = youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(thumb), resumable=False),
    )
    return request.execute()



def upload_video(
    *,
    video_path: str | Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "22",
    privacy_status: str = "private",
    publish_at: str | None = None,
    made_for_kids: bool = False,
    thumbnail_path: str | Path | None = None,
    client_secrets_file: str | Path | None = None,
    token_file: str | Path | None = None,
) -> YouTubeUploadResult:
    temp_files: list[str] = []

    video_ref = str(video_path).strip()
    local_video = Path(video_ref).expanduser()
    try:
        resolved_video_path = resolve_upload_file(video_ref, bucket_name=YOUTUBE_RENDER_BUCKET, suffix=local_video.suffix or ".mp4")
    except FileNotFoundError as exc:
        raise YouTubeUploadError(f"Video file not found: {video_ref}") from exc

    video = Path(resolved_video_path).expanduser()
    if not video.exists() or not video.is_file():
        raise YouTubeUploadError(f"Video file not found: {video_ref}")

    if not local_video.exists():
        temp_files.append(str(video))

    if not title.strip():
        raise YouTubeUploadError("title is required.")

    publish_at_value = validate_publish_at(publish_at)
    if publish_at_value and privacy_status != "private":
        raise YouTubeUploadError("Scheduled publishing requires privacy_status='private'.")

    youtube = get_youtube_service(
        client_secrets_file=client_secrets_file,
        token_file=token_file,
        allow_local_oauth=False,
    )

    body: dict[str, Any] = {
        "snippet": {
            "title": title.strip(),
            "description": description.strip(),
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }
    if publish_at_value:
        body["status"]["publishAt"] = publish_at_value

    media = MediaFileUpload(str(video), chunksize=-1, resumable=True)

    try:
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response: dict[str, Any] | None = None
        while response is None:
            _status, response = request.next_chunk()

        uploaded_video_id = str(response.get("id", "")).strip()
        if not uploaded_video_id:
            raise YouTubeUploadError("Upload completed but no video id was returned by YouTube.")

        thumb_response: dict[str, Any] | None = None
        if thumbnail_path:
            thumb_ref = str(thumbnail_path).strip()
            local_thumb = Path(thumb_ref).expanduser()
            try:
                resolved_thumbnail_path = resolve_upload_file(
                    thumb_ref,
                    bucket_name=YOUTUBE_THUMBNAIL_BUCKET,
                    suffix=local_thumb.suffix or ".png",
                )
            except FileNotFoundError as exc:
                raise YouTubeUploadError(f"Thumbnail file not found: {thumb_ref}") from exc

            if not local_thumb.exists():
                temp_files.append(resolved_thumbnail_path)
            thumb_response = set_thumbnail(youtube=youtube, video_id=uploaded_video_id, thumbnail_path=resolved_thumbnail_path)

        return YouTubeUploadResult(video_id=uploaded_video_id, response=response, thumbnail_response=thumb_response)
    except HttpError as exc:
        details = exc.content.decode("utf-8", errors="ignore") if getattr(exc, "content", None) else str(exc)
        raise YouTubeUploadError(f"YouTube API error: {details}") from exc
    except OSError as exc:
        raise YouTubeUploadError(f"File access error: {exc}") from exc
    finally:
        for temp_path in temp_files:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove temporary upload file: %s", temp_path)


if __name__ == "__main__":
    # Optional helper mode for local debugging in terminal:
    # YOUTUBE_RUN_OAUTH=1 python -m src.services.youtube_upload
    if os.getenv("YOUTUBE_RUN_OAUTH", "").strip() == "1":
        config = _resolve_auth_config()
        _run_oauth_flow(config.client_secrets_file, config.token_file)
        print(f"Saved YouTube token to {config.token_file}")
