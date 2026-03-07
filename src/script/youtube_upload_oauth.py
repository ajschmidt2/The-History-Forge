from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_FILE = Path("client_secrets.json")
TOKEN_FILE = Path("token.json")
VIDEO_FILE = Path("data/projects/.../renders/final.mp4")


def save_credentials(credentials: Credentials, token_file: Path = TOKEN_FILE) -> None:
    """Persist OAuth credentials so future runs can reuse refresh tokens."""
    token_file.write_text(credentials.to_json(), encoding="utf-8")


def load_valid_credentials(token_file: Path = TOKEN_FILE) -> Credentials | None:
    """Return credentials from token.json if they exist and are valid (or refreshable)."""
    if not token_file.exists():
        return None

    credentials = Credentials.from_authorized_user_info(
        json.loads(token_file.read_text(encoding="utf-8")),
        scopes=SCOPES,
    )

    if credentials.valid:
        return credentials

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_credentials(credentials, token_file)
        return credentials

    return None


def authenticate_with_local_server(
    client_secrets_file: Path = CLIENT_SECRETS_FILE,
    token_file: Path = TOKEN_FILE,
) -> Credentials:
    """Authenticate via OAuth consent flow on localhost:8080 and cache token.json."""
    credentials = load_valid_credentials(token_file)
    if credentials:
        return credentials

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_file), SCOPES)
    credentials = flow.run_local_server(
        host="localhost",
        port=8080,
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )
    save_credentials(credentials, token_file)
    return credentials


def upload_video(video_path: Path = VIDEO_FILE) -> str:
    """Upload a video to YouTube and return the uploaded video ID."""
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    credentials = authenticate_with_local_server()
    youtube = build("youtube", "v3", credentials=credentials)

    body = {
        "snippet": {
            "title": video_path.stem,
            "description": "Uploaded via google-api-python-client",
            "categoryId": "22",
        },
        "status": {"privacyStatus": "private"},
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    print(f"Starting resumable upload for: {video_path}")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"Upload complete. Video ID: {video_id}")
    return video_id


def main() -> None:
    try:
        upload_video()
    except HttpError as error:
        print(f"YouTube API error: {error}")
    except Exception as error:  # noqa: BLE001
        print(f"Upload failed: {error}")


if __name__ == "__main__":
    main()
