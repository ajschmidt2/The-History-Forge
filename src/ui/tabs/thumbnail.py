import traceback
from pathlib import Path

import streamlit as st
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from utils import generate_thumbnail_image, generate_thumbnail_prompt, generate_video_titles
from src.ui.state import generate_video_description_safe, openai_error_message, active_project_id


def tab_thumbnail_title() -> None:
    st.subheader("Thumbnail + title generator")
    st.caption("Generate YouTube title ideas, descriptions, hashtags, and thumbnail images for your project.")

    title_seed = st.text_input(
        "Title/topic seed",
        value=st.session_state.project_title or st.session_state.topic,
        placeholder="e.g., The Rise of Rome",
        key="thumbnail_title_seed",
    )
    title_count = st.slider("Number of title ideas", min_value=3, max_value=10, value=5, step=1)
    if st.button("Generate title ideas", width="stretch", key="thumbnail_generate_titles"):
        try:
            st.session_state.video_title_suggestions = generate_video_titles(
                title_seed,
                st.session_state.script_text,
                count=title_count,
            )
        except Exception as exc:  # noqa: BLE001 - surface title generation errors to user
            st.session_state.video_title_suggestions = []
            if isinstance(
                exc,
                (AuthenticationError, RateLimitError, APIConnectionError, APIError),
            ):
                tb = traceback.format_exc()
                st.error(f"{openai_error_message(exc)}\n\nTRACEBACK:\n{tb}")
                raise
            else:
                message = str(exc)
                if "invalid_api_key" in message or "Incorrect API key" in message:
                    st.error(
                        "Title generation failed: invalid OpenAI API key. "
                        "Set openai_api_key (or the Streamlit secret) and try again."
                    )
                else:
                    st.error(f"Title generation failed: {exc}")
        else:
            if st.session_state.video_title_suggestions:
                st.session_state.selected_video_title = st.session_state.video_title_suggestions[0]
            st.rerun()

    if st.session_state.video_title_suggestions:
        st.session_state.selected_video_title = st.radio(
            "Pick a title",
            st.session_state.video_title_suggestions,
            index=0,
            key="thumbnail_title_pick",
        )

    st.markdown("#### Description + hashtags")
    st.text_area(
        "Direction for description",
        height=110,
        placeholder="e.g., Focus on military strategy, keep tone serious, mention leadership lessons.",
        key="video_description_direction",
        help="Tell the AI what angle, tone, and key points you want in the description.",
    )
    hashtag_count = st.slider("Hashtag count", min_value=3, max_value=15, value=8, step=1)
    if st.button("Generate description + hashtags", width="stretch", key="thumbnail_generate_description"):
        try:
            st.session_state.video_description_text = generate_video_description_safe(
                topic=title_seed,
                title=st.session_state.selected_video_title,
                script=st.session_state.script_text,
                direction=st.session_state.get("video_description_direction", ""),
                hashtag_count=hashtag_count,
            )
        except Exception as exc:  # noqa: BLE001 - surface description generation errors to user
            if isinstance(
                exc,
                (AuthenticationError, RateLimitError, APIConnectionError, APIError),
            ):
                tb = traceback.format_exc()
                st.error(f"{openai_error_message(exc)}\n\nTRACEBACK:\n{tb}")
                raise
            else:
                st.error(f"Description generation failed: {exc}")
        else:
            st.toast("Video description generated.")
            st.rerun()

    st.text_area(
        "Video description (editable)",
        height=220,
        key="video_description_text",
        help="Edit this before copying into YouTube.",
    )

    style = st.selectbox(
        "Thumbnail style",
        ["Cinematic", "Documentary", "Dramatic lighting", "Vintage film", "Epic illustration"],
        index=0,
        key="thumbnail_style",
    )

    thumbnail_aspect_ratio = st.selectbox(
        "Thumbnail aspect ratio",
        ["16:9", "1:1", "9:16", "4:3"],
        index=["16:9", "1:1", "9:16", "4:3"].index(st.session_state.thumbnail_aspect_ratio)
        if st.session_state.thumbnail_aspect_ratio in ["16:9", "1:1", "9:16", "4:3"]
        else 0,
        key="thumbnail_aspect_ratio",
    )
    if st.button("Generate thumbnail prompt", width="stretch", key="thumbnail_prompt_btn"):
        try:
            st.session_state.thumbnail_prompt = generate_thumbnail_prompt(
                title_seed,
                st.session_state.selected_video_title,
                style,
            )
        except Exception as exc:  # noqa: BLE001 - surface thumbnail prompt errors to user
            if isinstance(
                exc,
                (AuthenticationError, RateLimitError, APIConnectionError, APIError),
            ):
                tb = traceback.format_exc()
                st.error(f"{openai_error_message(exc)}\n\nTRACEBACK:\n{tb}")
                raise
            else:
                message = str(exc)
                if "invalid_api_key" in message or "Incorrect API key" in message:
                    st.error(
                        "Thumbnail prompt generation failed: invalid OpenAI API key. "
                        "Set openai_api_key (or the Streamlit secret) and try again."
                    )
                else:
                    st.error(f"Thumbnail prompt generation failed: {exc}")
        else:
            st.rerun()

    if "thumbnail_prompt" not in st.session_state:
        st.session_state.thumbnail_prompt = ""
    st.text_area(
        "Thumbnail prompt",
        value=st.session_state.thumbnail_prompt,
        height=120,
        key="thumbnail_prompt",
    )

    if st.button("Create thumbnail image", width="stretch", key="thumbnail_generate_image"):
        image_bytes, err = generate_thumbnail_image(st.session_state.thumbnail_prompt, aspect_ratio=thumbnail_aspect_ratio)
        st.session_state.thumbnail_bytes = image_bytes
        st.session_state.thumbnail_error = err
        if err:
            st.error(err)
        else:
            project_folder = Path("data/projects") / active_project_id() / "assets/thumbnails"
            project_folder.mkdir(parents=True, exist_ok=True)
            output_path = project_folder / "thumbnail.png"
            output_path.write_bytes(image_bytes)
            st.session_state.thumbnail_saved_path = str(output_path)
            st.toast("Thumbnail generated.")
        st.rerun()

    if st.session_state.thumbnail_error:
        st.error(st.session_state.thumbnail_error)

    if st.session_state.thumbnail_bytes:
        st.image(st.session_state.thumbnail_bytes, caption="Generated thumbnail", width="stretch")
        if st.session_state.thumbnail_saved_path:
            st.caption(f"Saved to {st.session_state.thumbnail_saved_path}")
