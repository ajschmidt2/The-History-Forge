"""
src/ui/tabs/social_upload.py

Unified "Publish" tab — upload the rendered Shorts video to YouTube, Instagram,
and TikTok from a single screen.  Each platform lives in its own sub-tab.

Metadata (title, description, hashtags) is auto-filled from the project payload
that the automation workflow populates after generating a script.
"""

from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from src.config.secrets import get_secret
from src.services.instagram_upload import (
    InstagramUploadError,
    instagram_configured,
    upload_reel,
    validate_instagram_credentials,
    PRIVACY_OPTIONS as _IG_PRIVACY,
)
from src.services.tiktok_upload import (
    TikTokUploadError,
    tiktok_configured,
    upload_video as tiktok_upload_video,
    validate_tiktok_credentials,
    PRIVACY_OPTIONS as _TT_PRIVACY,
)
from src.services.youtube_oauth import build_youtube_auth_url
from src.services.youtube_upload import (
    YouTubeUploadError,
    exchange_code_for_token,
    upload_video as yt_upload_video,
    validate_youtube_credentials,
)
from src.ui.state import active_project_id, ensure_project_exists
from src.workflow.project_io import load_project_payload, save_project_payload

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _project_dir() -> Path:
    return ensure_project_exists(active_project_id())


def _default_video_path() -> Path:
    return _project_dir() / "renders" / "final.mp4"


def _default_thumbnail_path() -> Path:
    return _project_dir() / "thumbnail.png"


def _load_payload() -> dict:
    try:
        return load_project_payload(active_project_id())
    except Exception:  # noqa: BLE001
        return {}


def _load_metadata() -> dict:
    """Auto-fill title/description/tags/caption from the project payload."""
    payload = _load_payload()
    topic = str(payload.get("topic", "") or "").strip()

    title = str(payload.get("youtube_title", "") or "").strip()
    description = str(payload.get("youtube_description", "") or "").strip()
    raw_tags = payload.get("youtube_tags", [])

    if not title and topic:
        title = f"{topic} #shorts #history"
    if not description and topic:
        description = (
            f"{topic}\n\n"
            "Subscribe to History Crossroads for more 60-second history stories!"
        )
    if not raw_tags and topic:
        raw_tags = (
            [w.lower() for w in topic.split() if w.isalpha()]
            + ["history", "shorts", "historycrossroads", "historyfacts"]
        )

    tags_str = ", ".join(raw_tags) if isinstance(raw_tags, list) else str(raw_tags)

    # Instagram caption = description + hashtags on separate lines
    hashtag_line = " ".join(
        f"#{t.strip().lstrip('#')}"
        for t in (raw_tags if isinstance(raw_tags, list) else tags_str.split(","))
        if t.strip()
    )
    ig_caption = f"{description}\n\n{hashtag_line}".strip() if description else hashtag_line

    # TikTok title = shorter, plain text
    tt_title = title.replace(" #shorts #history", "").strip() or title

    return {
        "title": title or "History Forge Video",
        "description": description or "Created with The History Forge",
        "tags_str": tags_str or "history, shorts, historycrossroads",
        "ig_caption": ig_caption,
        "tt_title": tt_title or "History Forge Video",
        "payload": payload,
    }


def _save_upload_result(platform: str, result_id: str, url: str) -> None:
    """Persist the upload result to the project payload."""
    try:
        payload = _load_payload()
        payload[f"{platform}_video_id"] = result_id
        payload[f"{platform}_url"] = url
        save_project_payload(active_project_id(), payload)
    except Exception:  # noqa: BLE001
        pass


def _video_section() -> tuple[str, str]:
    """Render shared video / thumbnail path inputs. Returns (video_path, thumbnail_path)."""
    default_video = _default_video_path()
    default_thumb = _default_thumbnail_path()

    if default_video.exists():
        size_mb = default_video.stat().st_size / 1_048_576
        st.success(f"Video ready: `{default_video}` ({size_mb:.1f} MB)")
    else:
        st.info(
            f"No rendered video at `{default_video}` yet — "
            "run the full automation workflow first."
        )

    col_v, col_t = st.columns(2)
    with col_v:
        video_path = st.text_input(
            "Video file path", value=str(default_video), key="social_video_path"
        )
    with col_t:
        thumbnail_path = st.text_input(
            "Thumbnail (optional)", value=str(default_thumb), key="social_thumb_path"
        )
    return video_path, thumbnail_path


# ---------------------------------------------------------------------------
# YouTube sub-tab
# ---------------------------------------------------------------------------

def _render_youtube_tab(video_path: str, thumbnail_path: str, meta: dict) -> None:
    # ── Auth ──────────────────────────────────────────────────────────────────
    yt_client_secrets = get_secret("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
    yt_token_file = get_secret("YOUTUBE_TOKEN_FILE", "token.json")

    # Handle OAuth callback
    query_params = st.query_params
    oauth_code = query_params.get("code")
    oauth_state = query_params.get("state")
    if oauth_code:
        expected = st.session_state.get("yt_oauth_state")
        if expected and oauth_state == expected:
            st.session_state["yt_oauth_pending_code"] = oauth_code
            st.session_state["yt_oauth_redirect_uri"] = st.session_state.get(
                "yt_oauth_redirect_uri", ""
            )
        else:
            st.error("YouTube OAuth state mismatch — please try connecting again.")
        query_params.clear()
        st.rerun()

    if st.session_state.get("yt_oauth_pending_code"):
        st.warning("OAuth authorization received — click **Exchange Token** to finish.")
        if st.button("Exchange Token", type="primary", key="yt_exchange_btn"):
            _code = st.session_state.pop("yt_oauth_pending_code", "")
            _redir = st.session_state.pop("yt_oauth_redirect_uri", "")
            try:
                exchange_code_for_token(
                    _code, redirect_uri=_redir,
                    token_file=yt_token_file or None,
                )
                st.success("YouTube account connected.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Token exchange failed: {exc}")
        st.divider()

    is_authed, auth_msg = validate_youtube_credentials()
    if is_authed:
        st.success(f"Google account connected — {auth_msg}")
    else:
        st.error(f"Not connected: {auth_msg}")
        with st.expander("Connect YouTube Account", expanded=True):
            st.write(
                "Click below to authorize via Google OAuth. "
                "You'll be redirected to Google and then back here."
            )
            if st.button("Connect YouTube Account", key="yt_connect_btn"):
                try:
                    auth_url, state = build_youtube_auth_url()
                    st.session_state["yt_oauth_state"] = state
                    try:
                        import streamlit as _st
                        redir = str(_st.secrets["google_oauth"]["redirect_uri"])
                    except Exception:  # noqa: BLE001
                        redir = ""
                    st.session_state["yt_oauth_redirect_uri"] = redir
                    st.markdown(f"[Open Google authorization page]({auth_url})")
                    st.info("After approving, you'll be redirected back automatically.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not build authorization URL: {exc}")

    st.divider()

    # ── Metadata ──────────────────────────────────────────────────────────────
    st.markdown("#### Video Metadata")
    if st.button("Refresh from project", key="yt_refresh_meta"):
        for k in ["yt_title", "yt_desc", "yt_tags"]:
            st.session_state.pop(k, None)
        st.rerun()

    title = st.text_input(
        "Title", value=st.session_state.get("yt_title", meta["title"]), key="yt_title_input"
    )
    description = st.text_area(
        "Description",
        value=st.session_state.get("yt_desc", meta["description"]),
        height=100,
        key="yt_desc_input",
    )
    raw_tags = st.text_input(
        "Tags (comma-separated)",
        value=st.session_state.get("yt_tags", meta["tags_str"]),
        key="yt_tags_input",
    )

    st.divider()
    st.markdown("#### Publish Settings")
    col1, col2, col3 = st.columns(3)
    with col1:
        privacy = st.selectbox(
            "Privacy", ["private", "unlisted", "public"], index=0, key="yt_privacy"
        )
    with col2:
        category_id = st.text_input(
            "Category ID", value="27",
            help="27 = Education (best for Shorts). 22 = People & Blogs.",
            key="yt_category",
        )
    with col3:
        made_for_kids = st.checkbox("Made for kids", value=False, key="yt_kids")

    publish_at = st.text_input(
        "Schedule (optional ISO-8601 UTC)",
        value="",
        placeholder="e.g. 2026-03-20T18:00:00Z — requires Privacy = private",
        key="yt_publish_at",
    )

    st.divider()
    resolved_thumb: str | None = thumbnail_path.strip() or None
    if resolved_thumb and not Path(resolved_thumb).exists():
        resolved_thumb = None

    if st.button(
        "Upload to YouTube",
        type="primary",
        disabled=not is_authed,
        use_container_width=True,
        key="yt_upload_btn",
    ):
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        try:
            with st.spinner("Uploading to YouTube..."):
                result = yt_upload_video(
                    video_path=video_path.strip(),
                    title=title.strip(),
                    description=description.strip(),
                    tags=tags,
                    category_id=category_id.strip() or "27",
                    privacy_status=privacy,
                    publish_at=publish_at.strip() or None,
                    made_for_kids=made_for_kids,
                    thumbnail_path=resolved_thumb,
                    client_secrets_file=yt_client_secrets or None,
                    token_file=yt_token_file or None,
                )
            st.success(f"Uploaded! Video ID: `{result.video_id}`")
            yt_url = f"https://www.youtube.com/watch?v={result.video_id}"
            st.markdown(
                f"**Shorts URL:** https://www.youtube.com/shorts/{result.video_id}  \n"
                f"**Watch URL:** {yt_url}"
            )
            if result.thumbnail_response:
                st.info("Thumbnail uploaded.")
            _save_upload_result("youtube", result.video_id, yt_url)
        except YouTubeUploadError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Upload failed: {exc}")


# ---------------------------------------------------------------------------
# Instagram sub-tab
# ---------------------------------------------------------------------------

def _render_instagram_tab(video_path: str, meta: dict) -> None:
    is_authed, auth_msg = validate_instagram_credentials()
    if is_authed:
        st.success(f"Instagram connected — {auth_msg}")
    else:
        st.error(f"Not connected: {auth_msg}")
        with st.expander("How to connect Instagram", expanded=not is_authed):
            st.markdown(
                """
**Steps to get your Instagram credentials:**

1. Go to [developers.facebook.com](https://developers.facebook.com) and create a Meta Developer app
2. Add the **Instagram Graph API** product to your app
3. Connect your **Instagram Professional account** (Business or Creator)
4. Generate a **User Access Token** with scopes:
   - `instagram_content_publish`
   - `pages_read_engagement`
5. Exchange it for a **long-lived token** (60-day expiry):
   ```
   GET https://graph.facebook.com/v19.0/oauth/access_token
     ?grant_type=fb_exchange_token
     &client_id=YOUR_APP_ID
     &client_secret=YOUR_APP_SECRET
     &fb_exchange_token=SHORT_LIVED_TOKEN
   ```
6. Find your **Instagram User ID** (the numeric ID of your professional account)
7. Add to `.streamlit/secrets.toml`:
   ```toml
   INSTAGRAM_USER_ID = "123456789"
   INSTAGRAM_ACCESS_TOKEN = "EAAxxxx..."
   ```
                """
            )

    st.divider()

    # ── Metadata ──────────────────────────────────────────────────────────────
    st.markdown("#### Post Content")
    if st.button("Refresh from project", key="ig_refresh_meta"):
        st.session_state.pop("ig_caption", None)
        st.rerun()

    caption = st.text_area(
        "Caption (include hashtags)",
        value=st.session_state.get("ig_caption", meta["ig_caption"]),
        height=150,
        help="Instagram captions support hashtags inline. Max 2,200 characters.",
        key="ig_caption_input",
    )

    if len(caption) > 2200:
        st.warning(f"Caption is {len(caption)} characters — Instagram allows 2,200 max.")

    col1, col2 = st.columns(2)
    with col1:
        share_to_feed = st.checkbox(
            "Share Reel to main feed", value=True, key="ig_share_feed"
        )
    with col2:
        cover_ms = st.number_input(
            "Cover frame (ms)", min_value=0, value=1000, step=500, key="ig_cover_ms"
        )

    st.divider()
    st.info(
        "Instagram requires the video to be accessible via a **public URL**. "
        "If your project has a Supabase public URL (from the daily job), it will be used. "
        "Otherwise, the video will be uploaded to Supabase automatically before posting."
    )

    if st.button(
        "Upload to Instagram",
        type="primary",
        disabled=not is_authed,
        use_container_width=True,
        key="ig_upload_btn",
    ):
        payload = meta.get("payload", {})
        try:
            with st.spinner(
                "Uploading to Instagram... this takes 2-5 minutes while Instagram processes the video."
            ):
                result = upload_reel(
                    video_path=video_path.strip(),
                    caption=caption.strip(),
                    cover_timestamp_ms=int(cover_ms),
                    project_id=active_project_id(),
                    payload=payload,
                    share_to_feed=share_to_feed,
                )
            st.success(f"Reel posted! Media ID: `{result.media_id}`")
            if result.permalink:
                st.markdown(f"**Instagram URL:** {result.permalink}")
            _save_upload_result(
                "instagram", result.media_id, result.permalink or ""
            )
        except InstagramUploadError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Upload failed: {exc}")


# ---------------------------------------------------------------------------
# TikTok sub-tab
# ---------------------------------------------------------------------------

def _render_tiktok_tab(video_path: str, meta: dict) -> None:
    is_authed, auth_msg = validate_tiktok_credentials()
    if is_authed:
        st.success(f"TikTok connected — {auth_msg}")
    else:
        st.error(f"Not connected: {auth_msg}")
        with st.expander("How to connect TikTok", expanded=not is_authed):
            st.markdown(
                """
**Steps to get your TikTok credentials:**

1. Go to [developers.tiktok.com](https://developers.tiktok.com) and register a developer account
2. Create an app and enable the **Content Posting API** product
3. Request scopes: `video.upload` and `video.publish`
4. Complete the **OAuth 2.0** flow to get your `access_token` and `open_id`
5. Add to `.streamlit/secrets.toml`:
   ```toml
   TIKTOK_ACCESS_TOKEN = "act.xxxxx"
   TIKTOK_OPEN_ID = "xxxxx"
   ```

**Token refresh** (when the token expires):
```
POST https://open.tiktokapis.com/v2/oauth/token/
client_key=...&grant_type=refresh_token&refresh_token=...
```
                """
            )

    st.divider()

    # ── Metadata ──────────────────────────────────────────────────────────────
    st.markdown("#### Post Content")
    if st.button("Refresh from project", key="tt_refresh_meta"):
        st.session_state.pop("tt_title_val", None)
        st.rerun()

    tt_title = st.text_input(
        "Title / Caption",
        value=st.session_state.get("tt_title_val", meta["tt_title"]),
        help="Max 2,200 characters. Include hashtags directly in the title.",
        key="tt_title_input",
    )
    if len(tt_title) > 2200:
        st.warning(f"Title is {len(tt_title)} characters — TikTok allows 2,200 max.")

    st.divider()
    st.markdown("#### Post Settings")
    col1, col2 = st.columns(2)
    with col1:
        _privacy_labels = list(_TT_PRIVACY.keys())
        _privacy_default = "Private (self only)"  # safe default for testing
        _privacy_idx = _privacy_labels.index(_privacy_default)
        privacy_label = st.selectbox(
            "Privacy",
            _privacy_labels,
            index=_privacy_idx,
            key="tt_privacy",
            help="Start with 'Private (self only)' to verify before going public.",
        )
        privacy_level = _TT_PRIVACY[privacy_label]

    with col2:
        cover_ms = st.number_input(
            "Cover frame (ms)", min_value=0, value=1000, step=500, key="tt_cover_ms"
        )

    col3, col4, col5 = st.columns(3)
    with col3:
        disable_comment = st.checkbox("Disable comments", value=False, key="tt_no_comment")
    with col4:
        disable_duet = st.checkbox("Disable duet", value=False, key="tt_no_duet")
    with col5:
        disable_stitch = st.checkbox("Disable stitch", value=False, key="tt_no_stitch")

    st.divider()

    if st.button(
        "Upload to TikTok",
        type="primary",
        disabled=not is_authed,
        use_container_width=True,
        key="tt_upload_btn",
    ):
        try:
            with st.spinner(
                "Uploading to TikTok... uploading chunks then waiting for processing."
            ):
                result = tiktok_upload_video(
                    video_path=video_path.strip(),
                    title=tt_title.strip(),
                    privacy_level=privacy_level,
                    disable_comment=disable_comment,
                    disable_duet=disable_duet,
                    disable_stitch=disable_stitch,
                    video_cover_timestamp_ms=int(cover_ms),
                )
            st.success(f"TikTok video posted! Publish ID: `{result.publish_id}`")
            if result.share_url:
                st.markdown(f"**TikTok URL:** {result.share_url}")
            _save_upload_result(
                "tiktok", result.publish_id, result.share_url or ""
            )
        except TikTokUploadError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Upload failed: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def tab_social_upload() -> None:
    st.subheader("Publish")
    st.caption(
        "Upload your rendered video to YouTube, Instagram, and TikTok. "
        "Metadata is auto-filled from the automation workflow."
    )

    # ── Shared: video path + metadata ─────────────────────────────────────────
    video_path, thumbnail_path = _video_section()
    meta = _load_metadata()

    # Show a "Refresh metadata" button at the top level too
    if st.button("Refresh all metadata from project", key="social_refresh_all"):
        for k in ["yt_title", "yt_desc", "yt_tags", "ig_caption", "tt_title_val"]:
            st.session_state.pop(k, None)
        st.rerun()

    # Auth status overview
    _yt_ok, _ = validate_youtube_credentials()
    try:
        _ig_ok = instagram_configured()
    except Exception as exc:  # noqa: BLE001
        log.warning("social_upload: instagram_configured() failed; continuing: %s", exc)
        _ig_ok = False
    _tt_ok = tiktok_configured()

    col_yt, col_ig, col_tt = st.columns(3)
    col_yt.metric(
        "YouTube",
        "Connected" if _yt_ok else "Not connected",
        delta=None,
        help="Google OAuth token status",
    )
    col_ig.metric(
        "Instagram",
        "Configured" if _ig_ok else "Not configured",
        delta=None,
        help="INSTAGRAM_USER_ID + INSTAGRAM_ACCESS_TOKEN",
    )
    col_tt.metric(
        "TikTok",
        "Configured" if _tt_ok else "Not configured",
        delta=None,
        help="TIKTOK_ACCESS_TOKEN",
    )

    st.divider()

    # ── Platform sub-tabs ─────────────────────────────────────────────────────
    yt_tab, ig_tab, tt_tab = st.tabs(["▶️ YouTube", "📸 Instagram", "♪ TikTok"])

    with yt_tab:
        _render_youtube_tab(video_path, thumbnail_path, meta)

    with ig_tab:
        _render_instagram_tab(video_path, meta)

    with tt_tab:
        _render_tiktok_tab(video_path, meta)
