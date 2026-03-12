from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.config.secrets import get_secret
from src.services.youtube_upload import YouTubeUploadError, upload_video, validate_youtube_credentials
from src.ui.state import active_project_id, ensure_project_exists


def _default_video_path() -> Path:
    project_dir = ensure_project_exists(active_project_id())
    return project_dir / "renders" / "final.mp4"


def _default_thumbnail_path() -> Path:
    project_dir = ensure_project_exists(active_project_id())
    return project_dir / "thumbnail.png"


def tab_youtube_upload() -> None:
    st.subheader("YouTube Uploader")
    st.caption("Upload a rendered video with OAuth token refresh, scheduling, tags, and optional thumbnail.")

    default_video_path = _default_video_path()
    default_thumbnail_path = _default_thumbnail_path()

    env_client_secrets = get_secret("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
    env_token_file = get_secret("YOUTUBE_TOKEN_FILE", "token.json")

    st.write(f"Detected default render path: `{default_video_path}`")
    st.write(f"Detected default thumbnail path: `{default_thumbnail_path}`")

    client_secrets_file = st.text_input("OAuth client secrets path", value=env_client_secrets)
    token_file = st.text_input("OAuth token path", value=env_token_file)

    validate_col, _ = st.columns([1, 3])
    with validate_col:
        if st.button("Validate credentials"):
            ok, message = validate_youtube_credentials(
                client_secrets_file=client_secrets_file.strip() or None,
                token_file=token_file.strip() or None,
            )
            if ok:
                st.success(message)
            else:
                st.warning(message)

    video_path = st.text_input("Video file path", value=str(default_video_path))
    thumbnail_path = st.text_input("Thumbnail file path (optional)", value=str(default_thumbnail_path))

    title = st.text_input("Video title", value=st.session_state.get("project_title", "History Forge Video"))
    description = st.text_area("Description", value="Created with The History Forge")
    raw_tags = st.text_input("Tags (comma-separated)", value="history,ai,storytelling")

    col1, col2, col3 = st.columns(3)
    with col1:
        privacy_status = st.selectbox("Privacy", ["private", "unlisted", "public"], index=0)
    with col2:
        category_id = st.text_input("Category ID", value="22", help="22 = People & Blogs")
    with col3:
        made_for_kids = st.checkbox("Made for kids", value=False)

    publish_at = st.text_input(
        "Publish at (optional ISO-8601 UTC)",
        value="",
        help="Example: 2026-03-14T18:30:00Z. Requires privacy set to private.",
    )

    st.caption("Preview")
    st.code(
        "\n".join(
            [
                f"video_path={video_path}",
                f"thumbnail_path={thumbnail_path or '<none>'}",
                f"privacy_status={privacy_status}",
                f"publish_at={publish_at or '<none>'}",
            ]
        )
    )

    if st.button("Upload to YouTube", type="primary", width="stretch"):
        tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
        resolved_thumbnail: str | None = thumbnail_path.strip() or None

        try:
            with st.spinner("Uploading video to YouTube..."):
                result = upload_video(
                    video_path=video_path.strip(),
                    title=title,
                    description=description,
                    tags=tags,
                    category_id=category_id.strip() or "22",
                    privacy_status=privacy_status,
                    publish_at=publish_at.strip() or None,
                    made_for_kids=made_for_kids,
                    thumbnail_path=resolved_thumbnail,
                    client_secrets_file=client_secrets_file.strip() or None,
                    token_file=token_file.strip() or None,
                )

            st.success(f"YouTube upload complete. Video ID: {result.video_id}")
            st.markdown(f"Video URL: https://www.youtube.com/watch?v={result.video_id}")
            st.json(result.response)

            if result.thumbnail_response is not None:
                st.info("Thumbnail uploaded successfully.")
                st.json(result.thumbnail_response)
        except YouTubeUploadError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Unexpected YouTube upload failure: {exc}")
