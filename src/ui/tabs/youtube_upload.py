from __future__ import annotations

import json
from pathlib import Path

import requests
import streamlit as st

from src.ui.state import active_project_id, ensure_project_exists


YOUTUBE_RESUMABLE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=resumable"


def _default_video_path() -> Path:
    project_dir = ensure_project_exists(active_project_id())
    return project_dir / "renders" / "final.mp4"


def _start_resumable_upload(
    *,
    access_token: str,
    title: str,
    description: str,
    privacy_status: str,
    category_id: str,
    tags: list[str],
    video_size: int,
    mime_type: str,
) -> str:
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
            "tags": tags,
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }

    response = requests.post(
        YOUTUBE_RESUMABLE_UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(video_size),
            "X-Upload-Content-Type": mime_type,
        },
        data=json.dumps(metadata),
        timeout=60,
    )
    response.raise_for_status()

    upload_url = response.headers.get("Location", "").strip()
    if not upload_url:
        raise RuntimeError("YouTube did not return a resumable upload URL.")
    return upload_url


def _upload_video_bytes(*, upload_url: str, mime_type: str, payload: bytes) -> dict[str, object]:
    response = requests.put(
        upload_url,
        headers={"Content-Type": mime_type},
        data=payload,
        timeout=600,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def tab_youtube_upload() -> None:
    st.subheader("YouTube Uploader")
    st.caption("Upload your final video directly to YouTube with an OAuth access token.")

    default_video_path = _default_video_path()
    st.write(f"Default render path: `{default_video_path}`")

    access_token = st.text_input(
        "YouTube OAuth Access Token",
        type="password",
        help="Use an OAuth 2.0 token with youtube.upload scope.",
    )
    video_source = st.radio("Video source", ["Use rendered final.mp4", "Upload video file"], horizontal=True)

    video_bytes: bytes | None = None
    file_name = "final.mp4"
    mime_type = "video/mp4"

    if video_source == "Use rendered final.mp4":
        if default_video_path.exists():
            video_bytes = default_video_path.read_bytes()
        else:
            st.warning("No final.mp4 found yet. Render a video first or choose Upload video file.")
    else:
        uploaded_file = st.file_uploader("Video file", type=["mp4", "mov", "mkv", "webm"])
        if uploaded_file is not None:
            video_bytes = uploaded_file.getvalue()
            file_name = uploaded_file.name or file_name
            mime_type = uploaded_file.type or mime_type

    title = st.text_input("Video title", value=st.session_state.get("project_title", "History Forge Video"))
    description = st.text_area("Description", value="Created with The History Forge")
    privacy_status = st.selectbox("Privacy", ["private", "unlisted", "public"], index=1)
    category_id = st.text_input("Category ID", value="22", help="22 = People & Blogs")
    raw_tags = st.text_input("Tags (comma-separated)", value="history,ai,storytelling")

    if st.button("Upload to YouTube", type="primary", width="stretch"):
        if not access_token.strip():
            st.error("Please provide a YouTube OAuth access token.")
            return
        if not video_bytes:
            st.error("Please provide a video file to upload.")
            return
        if not title.strip():
            st.error("Please provide a title.")
            return

        tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]

        try:
            with st.spinner("Starting YouTube upload session..."):
                upload_url = _start_resumable_upload(
                    access_token=access_token.strip(),
                    title=title.strip(),
                    description=description.strip(),
                    privacy_status=privacy_status,
                    category_id=category_id.strip() or "22",
                    tags=tags,
                    video_size=len(video_bytes),
                    mime_type=mime_type,
                )

            with st.spinner("Uploading video bytes to YouTube..."):
                result = _upload_video_bytes(upload_url=upload_url, mime_type=mime_type, payload=video_bytes)

            video_id = str(result.get("id", "")).strip()
            st.success(f"Uploaded {file_name} to YouTube successfully.")
            if video_id:
                st.markdown(f"Video URL: https://www.youtube.com/watch?v={video_id}")
            st.json(result)
        except requests.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            st.error("YouTube upload failed.")
            st.code(detail)
        except Exception as exc:  # noqa: BLE001
            st.error(f"YouTube upload failed: {exc}")
