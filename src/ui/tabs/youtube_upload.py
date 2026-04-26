from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.config.secrets import get_secret
from src.services.youtube_oauth import build_youtube_auth_url, resolve_youtube_redirect_uri
from src.services.youtube_upload import (
    YouTubeUploadError,
    exchange_code_for_token,
    run_local_oauth_sign_in,
    upload_video,
    validate_youtube_credentials,
)
from src.ui.state import active_project_id, ensure_project_exists
from src.workflow.project_io import load_project_payload


def _project_dir() -> Path:
    return ensure_project_exists(active_project_id())


def _default_video_path() -> Path:
    return _project_dir() / "renders" / "final.mp4"


def _default_thumbnail_path() -> Path:
    return _project_dir() / "thumbnail.png"


def _auth_status() -> tuple[bool, str]:
    ok, msg = validate_youtube_credentials()
    return ok, msg


def _load_youtube_defaults() -> dict:
    try:
        payload = load_project_payload(active_project_id())
    except Exception:  # noqa: BLE001
        payload = {}

    topic = str(payload.get("topic", "") or "").strip()
    title = str(payload.get("youtube_title", "") or "").strip()
    description = str(payload.get("youtube_description", "") or "").strip()
    raw_tags = payload.get("youtube_tags", [])

    if not title and topic:
        title = f"{topic} #shorts #history"
    if not description and topic:
        description = f"{topic}\n\nSubscribe to History Crossroads for more 60-second history stories!"
    if not raw_tags and topic:
        raw_tags = [w.lower() for w in topic.split() if w.isalpha()] + [
            "history",
            "shorts",
            "historycrossroads",
            "historyfacts",
        ]

    tags_str = ", ".join(raw_tags) if isinstance(raw_tags, list) else str(raw_tags)
    return {
        "title": title or "History Forge Video",
        "description": description or "Created with The History Forge",
        "tags": tags_str or "history, shorts, historycrossroads",
    }


def tab_youtube_upload() -> None:
    st.subheader("YouTube Upload")

    client_secrets_file = get_secret("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
    token_file = get_secret("YOUTUBE_TOKEN_FILE", "token.json")

    query_params = st.query_params
    oauth_code = query_params.get("code")
    oauth_returned_state = query_params.get("state")

    if oauth_code:
        expected_state = st.session_state.get("youtube_oauth_state")
        if expected_state and oauth_returned_state and oauth_returned_state != expected_state:
            st.error("OAuth state mismatch — please click 'Connect YouTube Account' and try again.")
            query_params.clear()
            st.session_state.pop("youtube_oauth_state", None)
            st.rerun()

        try:
            exchange_code_for_token(
                oauth_code,
                redirect_uri=resolve_youtube_redirect_uri(),
                token_file=token_file or None,
            )
            query_params.clear()
            st.session_state.pop("youtube_oauth_state", None)
            st.success("YouTube account connected successfully.")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Token exchange failed: {exc}")
            query_params.clear()
            st.session_state.pop("youtube_oauth_state", None)
            st.rerun()

    is_authed, auth_msg = _auth_status()

    if is_authed:
        st.success(f"Google account connected — {auth_msg}")
    else:
        st.error(f"Not connected: {auth_msg}")
        with st.expander("Connect YouTube Account", expanded=True):
            st.write(
                "Click below to authorize The History Forge to upload videos on your behalf. "
                "You will be redirected to Google and then back here."
            )
            if st.button("Connect YouTube Account"):
                try:
                    auth_url, state = build_youtube_auth_url()
                    st.session_state["youtube_oauth_state"] = state
                    st.markdown(f"[Open Google authorization page]({auth_url})", unsafe_allow_html=False)
                    st.info("After approving, you will be redirected back to this page automatically.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not build authorization URL: {exc}")
            st.caption("If you're using the desktop app, the local desktop sign-in is usually more reliable.")
            if st.button("Connect YouTube (Desktop)", key="youtube_upload_connect_desktop"):
                try:
                    st.info("A browser window should open for Google sign-in. After approval, Google will return to a localhost page and then back here.")
                    with st.spinner("Opening local YouTube sign-in in your browser..."):
                        token_path = run_local_oauth_sign_in(
                            client_secrets_file=client_secrets_file or None,
                            token_file=token_file or None,
                        )
                    st.success(f"YouTube desktop sign-in completed. Token saved to {token_path}.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Desktop YouTube sign-in failed: {exc}")

    st.divider()

    default_video = _default_video_path()
    default_thumb = _default_thumbnail_path()

    if default_video.exists():
        st.success(f"Video ready: `{default_video}`")
    else:
        st.info(f"No rendered video found yet at `{default_video}` — run the full workflow first.")

    col_vid, col_thumb = st.columns(2)
    with col_vid:
        video_path = st.text_input("Video file path", value=str(default_video))
    with col_thumb:
        thumbnail_path = st.text_input("Thumbnail path (optional)", value=str(default_thumb))

    st.divider()
    st.markdown("#### Video Metadata")

    defaults = _load_youtube_defaults()

    if st.button("Refresh metadata from project"):
        for key in ["yt_title_override", "yt_desc_override", "yt_tags_override"]:
            st.session_state.pop(key, None)
        st.rerun()

    title = st.text_input(
        "Title",
        value=st.session_state.get("yt_title_override", defaults["title"]),
        key="yt_upload_title",
    )
    description = st.text_area(
        "Description",
        value=st.session_state.get("yt_desc_override", defaults["description"]),
        height=100,
        key="yt_upload_description",
    )
    raw_tags = st.text_input(
        "Hashtags / Tags (comma-separated)",
        value=st.session_state.get("yt_tags_override", defaults["tags"]),
        key="yt_upload_tags",
    )

    st.divider()
    st.markdown("#### Publish Settings")
    col1, col2, col3 = st.columns(3)
    with col1:
        privacy_status = st.selectbox("Privacy", ["private", "unlisted", "public"], index=0)
    with col2:
        category_id = st.text_input(
            "Category ID",
            value="27",
            help="27 = Education (best for Shorts). 22 = People & Blogs.",
        )
    with col3:
        made_for_kids = st.checkbox("Made for kids", value=False)

    publish_at = st.text_input(
        "Schedule publish (optional, ISO-8601 UTC)",
        value="",
        placeholder="e.g. 2026-03-20T18:00:00Z — requires Privacy = private",
    )

    st.divider()
    if not is_authed:
        st.warning("Connect your YouTube account above before uploading.")

    upload_disabled = not is_authed
    if st.button("Upload to YouTube", type="primary", disabled=upload_disabled, use_container_width=True):
        tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
        resolved_thumbnail: str | None = thumbnail_path.strip() or None
        if resolved_thumbnail and not Path(resolved_thumbnail).exists():
            resolved_thumbnail = None

        try:
            with st.spinner("Uploading to YouTube... this may take a minute."):
                result = upload_video(
                    video_path=video_path.strip(),
                    title=title.strip(),
                    description=description.strip(),
                    tags=tags,
                    category_id=category_id.strip() or "27",
                    privacy_status=privacy_status,
                    publish_at=publish_at.strip() or None,
                    made_for_kids=made_for_kids,
                    thumbnail_path=resolved_thumbnail,
                    client_secrets_file=client_secrets_file or None,
                    token_file=token_file or None,
                )

            st.success(f"Uploaded! Video ID: `{result.video_id}`")
            st.markdown(
                f"**YouTube URL:** https://www.youtube.com/shorts/{result.video_id}  \n"
                f"**Full URL:** https://www.youtube.com/watch?v={result.video_id}"
            )
            if result.thumbnail_response:
                st.info("Thumbnail uploaded successfully.")

            try:
                from src.workflow.project_io import load_project_payload, save_project_payload

                payload = load_project_payload(active_project_id())
                payload["youtube_video_id"] = result.video_id
                payload["youtube_url"] = f"https://www.youtube.com/watch?v={result.video_id}"
                save_project_payload(active_project_id(), payload)
            except Exception:  # noqa: BLE001
                pass

        except YouTubeUploadError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Upload failed: {exc}")
