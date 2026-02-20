import json
import re
import zipfile
from collections import deque
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import streamlit as st
import utils as forge_utils
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError
from PIL import Image, ImageDraw, ImageFont

from utils import (
    Scene,
    generate_lucky_topic,
    generate_script,
    split_script_into_scenes,
    generate_prompts_for_scenes,
    generate_image_for_scene,
    generate_voiceover,
    generate_thumbnail_image,
    generate_thumbnail_prompt,
    generate_video_description,
    generate_video_titles,
)
from src.storage import record_asset, record_assets, upsert_project
from src.video.ffmpeg_render import render_video_from_timeline
from src.video.timeline_builder import build_default_timeline, write_timeline_json
from src.video.timeline_schema import CaptionStyle, Music, Timeline, Voiceover
from src.video.utils import FFmpegNotFoundError, ensure_ffmpeg_exists


# ----------------------------
# Auth gate (uses Streamlit secrets)
# ----------------------------

def require_passcode() -> None:
    secret_key = "APP_PASSCODE" if "APP_PASSCODE" in st.secrets else "password"
    expected = st.secrets.get(secret_key, "")

    if not expected:
        return

    st.session_state.setdefault("auth_ok", False)
    if st.session_state.auth_ok:
        return

    st.title("🔒 The History Forge")
    code = st.text_input("Password", type="password")
    if st.button("Log in", type="primary"):
        st.session_state.auth_ok = code == expected
        if not st.session_state.auth_ok:
            st.error("Incorrect password.")
        st.rerun()
    st.stop()


# ----------------------------
# State
# ----------------------------

PREFERENCES_PATH = Path("data/user_preferences.json")


def _load_saved_voice_id() -> str:
    if not PREFERENCES_PATH.exists():
        return ""
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    voice_id = data.get("voice_id", "") if isinstance(data, dict) else ""
    return str(voice_id).strip()


def _save_voice_id(voice_id: str) -> None:
    sanitized = (voice_id or "").strip()
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"voice_id": sanitized}
    PREFERENCES_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def init_state() -> None:
    st.session_state.setdefault("project_title", "Untitled Project")
    st.session_state.setdefault("topic", "")
    st.session_state.setdefault("script_text", "")
    st.session_state.setdefault("script_text_input", "")
    st.session_state.setdefault("pending_script_text_input", "")
    if st.session_state.script_text and not st.session_state.script_text_input:
        st.session_state.script_text_input = st.session_state.script_text

    st.session_state.setdefault("tone", "Documentary")
    st.session_state.setdefault("length", "8–10 minutes")

    st.session_state.setdefault("visual_style", "Photorealistic cinematic")
    st.session_state.setdefault("aspect_ratio", "16:9")
    st.session_state.setdefault("variations_per_scene", 1)

    st.session_state.setdefault("max_scenes", 12)
    st.session_state.setdefault("scenes", [])

    st.session_state.setdefault("voice_id", _load_saved_voice_id())
    st.session_state.setdefault("voiceover_bytes", None)
    st.session_state.setdefault("voiceover_error", None)
    st.session_state.setdefault("voiceover_saved_path", "")
    st.session_state.setdefault("video_title_suggestions", [])
    st.session_state.setdefault("selected_video_title", "")
    st.session_state.setdefault("thumbnail_prompt", "")
    st.session_state.setdefault("thumbnail_bytes", None)
    st.session_state.setdefault("thumbnail_error", None)
    st.session_state.setdefault("thumbnail_saved_path", "")
    st.session_state.setdefault("thumbnail_aspect_ratio", "16:9")
    st.session_state.setdefault("video_description_direction", "")
    st.session_state.setdefault("video_description_text", "")


def _project_folder_name() -> str:
    return (st.session_state.project_title or "Untitled Project").strip().replace(" ", "_")


def scenes_ready() -> bool:
    return isinstance(st.session_state.scenes, list) and len(st.session_state.scenes) > 0


def script_ready() -> bool:
    return bool((st.session_state.script_text or "").strip())


def clear_downstream(after: str) -> None:
    """
    Clear downstream artifacts when upstream changes.
    after = "script" clears scenes/prompts/images/voiceover.
    after = "scenes" clears prompts/images.
    after = "prompts" clears images.
    """
    if after in ("script",):
        st.session_state.scenes = []
        st.session_state.voiceover_bytes = None
        st.session_state.voiceover_error = None

    if after in ("script", "scenes"):
        if isinstance(st.session_state.scenes, list):
            for s in st.session_state.scenes:
                if isinstance(s, Scene):
                    s.image_prompt = ""
                    s.image_bytes = None
                    s.image_variations = []

    if after in ("script", "scenes", "prompts"):
        if isinstance(st.session_state.scenes, list):
            for s in st.session_state.scenes:
                if isinstance(s, Scene):
                    s.image_bytes = None
                    s.image_variations = []
                    s.primary_image_index = 0
                    s.image_error = ""


def _openai_error_message(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return (
            "OpenAI authentication failed. Check that your API key is valid and set as "
            "`openai_api_key` (or `OPENAI_API_KEY`) in Streamlit secrets."
        )
    if isinstance(exc, RateLimitError):
        return (
            "OpenAI rate limit or quota exceeded. Verify your usage limits and billing status."
        )
    if isinstance(exc, APIConnectionError):
        return "OpenAI connection failed. Please check your network and try again."
    if isinstance(exc, APIError):
        return f"OpenAI API error: {exc}"
    return f"OpenAI request failed: {exc}"


def _generate_video_description_fallback(topic: str, title: str, direction: str, hashtag_count: int) -> str:
    base = (title or topic or "This history story").strip()
    creator_direction = (direction or "").strip()
    direction_line = f" Angle: {creator_direction}" if creator_direction else ""
    hashtags = ["#History", "#Documentary", "#Storytelling", "#WorldHistory", "#HistoricalFacts"]
    hashtags_text = " ".join(hashtags[: max(1, min(hashtag_count, len(hashtags)))])
    return (
        f"{base} changed the course of history in ways most people never hear about. "
        "In this episode, we break down the key events, major figures, and why this story still matters today."
        f"{direction_line}\n\n"
        "If you enjoyed this story, subscribe for more history deep-dives.\n\n"
        f"{hashtags_text}"
    )


def generate_video_description_safe(
    topic: str,
    title: str,
    script: str,
    direction: str,
    hashtag_count: int,
) -> str:
    generator = getattr(forge_utils, "generate_video_description", None)
    if callable(generator):
        return generator(
            topic=topic,
            title=title,
            script=script,
            direction=direction,
            hashtag_count=hashtag_count,
        )
    return _generate_video_description_fallback(topic, title, direction, hashtag_count)


# ----------------------------
# Tabs
# ----------------------------

def tab_paste_script() -> None:
    st.subheader("Paste your own script")

    if st.session_state.pending_script_text_input:
        st.session_state.script_text_input = st.session_state.pending_script_text_input
        st.session_state.pending_script_text_input = ""

    st.session_state.project_title = st.text_input(
        "Project Title",
        value=st.session_state.project_title,
        placeholder="e.g., The Rise of Rome",
    )

    new_script = st.text_area(
        "Script",
        key="script_text_input",
        height=320,
        placeholder="Paste your narration script here...",
    )

    if st.button("Use this script →", type="primary", width="stretch"):
        st.session_state.script_text = st.session_state.script_text_input
        clear_downstream("script")
        st.toast("Script loaded.")
        st.rerun()


def tab_generate_script() -> None:
    st.subheader("Generate script")

    c1, c2 = st.columns([3, 1])
    with c1:
        st.session_state.topic = st.text_input(
            "Topic",
            value=st.session_state.topic,
            placeholder="e.g., The Rise of Rome",
        )
    with c2:
        if st.button("🎲 I'm Feeling Lucky", width="stretch"):
            try:
                st.session_state.topic = generate_lucky_topic()
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                st.error(_openai_error_message(exc))
                return
            st.session_state.project_title = st.session_state.topic
            st.toast(st.session_state.topic)
            clear_downstream("script")

    st.session_state.length = st.selectbox(
        "Length",
        ["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"],
        index=["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"].index(st.session_state.length)
        if st.session_state.length in ["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"]
        else 1,
    )
    st.session_state.tone = st.selectbox(
        "Tone",
        ["Documentary", "Cinematic", "Mysterious", "Playful"],
        index=["Documentary", "Cinematic", "Mysterious", "Playful"].index(st.session_state.tone)
        if st.session_state.tone in ["Documentary", "Cinematic", "Mysterious", "Playful"]
        else 0,
    )

    if st.button("Generate Script", type="primary", width="stretch"):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic or use I'm Feeling Lucky.")
            return
        with st.spinner("Generating script..."):
            try:
                generated_script = generate_script(
                    topic=st.session_state.topic,
                    length=st.session_state.length,
                    tone=st.session_state.tone,
                )
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                st.error(_openai_error_message(exc))
                return
        st.session_state.script_text = generated_script
        st.session_state.pending_script_text_input = generated_script
        st.session_state.project_title = st.session_state.topic or st.session_state.project_title
        clear_downstream("script")
        st.toast("Script generated.")
        st.rerun()

    if script_ready():
        with st.expander("Preview script", expanded=False):
            st.write(st.session_state.script_text)


def tab_create_scenes() -> None:
    st.subheader("Create scenes")

    if not script_ready():
        st.warning("Paste or generate a script first.")
        return

    st.session_state.max_scenes = st.number_input(
        "Number of scenes",
        min_value=3,
        max_value=75,
        value=int(st.session_state.max_scenes),
        step=1,
    )

    if st.button("Split script into scenes", type="primary", width="stretch"):
        with st.spinner("Splitting script..."):
            st.session_state.scenes = split_script_into_scenes(
                st.session_state.script_text,
                max_scenes=int(st.session_state.max_scenes),
            )
        clear_downstream("scenes")
        st.toast(f"Created {len(st.session_state.scenes)} scenes.")
        st.rerun()

    if not scenes_ready():
        st.info("No scenes yet.")
        return

    st.divider()
    st.markdown("### Scene list (editable)")
    for s in st.session_state.scenes:
        with st.expander(f"{s.index:02d} — {s.title}", expanded=False):
            s.title = st.text_input("Title", value=s.title, key=f"title_{s.index}")
            s.script_excerpt = st.text_area("Excerpt", value=s.script_excerpt, height=140, key=f"txt_{s.index}")
            s.visual_intent = st.text_area("Visual intent", value=s.visual_intent, height=90, key=f"vi_{s.index}")

    st.caption("Tip: prompts + images are generated in the next tabs.")


def tab_create_prompts() -> None:
    st.subheader("Create prompts")

    if not scenes_ready():
        st.warning("Create scenes first.")
        return

    style_options = [
        "Photorealistic cinematic",
        "Painterly",
        "Vintage photo",
        "Illustrated",
        "Film still",
        "Sepia archival",
        "Watercolor",
        "Oil painting",
        "Graphic novel",
        "3D render",
        "Epic concept art",
        "High-contrast noir",
        "Vintage postcard",
    ]
    current_style = st.session_state.visual_style if st.session_state.visual_style in style_options else style_options[0]
    st.session_state.visual_style = st.selectbox(
        "Visual style",
        style_options,
        index=style_options.index(current_style),
    )

    if st.button("Generate prompts for all scenes", type="primary", width="stretch"):
        with st.spinner("Generating prompts..."):
            st.session_state.scenes = generate_prompts_for_scenes(
                st.session_state.scenes,
                tone=st.session_state.tone,
                style=st.session_state.visual_style,
            )
            for s in st.session_state.scenes:
                st.session_state[f"prompt_{s.index}"] = s.image_prompt
        clear_downstream("prompts")
        st.toast("Prompts generated.")
        st.rerun()

    st.divider()
    for s in st.session_state.scenes:
        s.image_prompt = st.text_area(
            f"{s.index:02d} — {s.title} prompt",
            value=s.image_prompt or "",
            height=110,
            key=f"prompt_{s.index}",
        )


def _save_scene_image_bytes(scene: Scene, image_bytes: bytes) -> None:
    scene.image_bytes = image_bytes
    scene.image_variations = [image_bytes]
    scene.primary_image_index = 0
    scene.image_error = ""

    images_dir = Path("data/projects") / _project_folder_name() / "assets/images"
    images_dir.mkdir(parents=True, exist_ok=True)
    destination = images_dir / f"s{scene.index:02d}.png"
    destination.write_bytes(image_bytes)
    record_asset(_project_folder_name(), "image", destination)


def tab_create_images() -> None:
    st.subheader("Create images")

    if not scenes_ready():
        st.warning("Create scenes first.")
        return

    st.info(
        "You can generate images with AI, upload your own image for each scene, or bulk upload scene images."
    )

    aspect_ratio_options = ["16:9", "9:16", "1:1"]
    current_aspect_ratio = (
        st.session_state.aspect_ratio
        if st.session_state.aspect_ratio in aspect_ratio_options
        else aspect_ratio_options[0]
    )
    st.session_state.aspect_ratio = st.selectbox(
        "Aspect ratio",
        aspect_ratio_options,
        index=aspect_ratio_options.index(current_aspect_ratio),
    )
    st.session_state.variations_per_scene = st.slider(
        "Variations per scene",
        1,
        4,
        int(st.session_state.variations_per_scene),
    )

    bulk_uploads = st.file_uploader(
        "Bulk upload scene images (optional, ordered by scene number)",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="bulk_scene_image_upload",
        help="When uploaded, files are assigned to scenes in order: first file -> Scene 1, second -> Scene 2, etc.",
    )
    if bulk_uploads:
        applied = 0
        for scene, upload in zip(st.session_state.scenes, bulk_uploads):
            _save_scene_image_bytes(scene, upload.getvalue())
            applied += 1
        st.success(f"Applied {applied} uploaded image(s) to scenes and saved them to assets/images.")
        st.rerun()

    if st.button("Generate images for all scenes", type="primary", width="stretch"):
        with st.spinner("Generating images..."):
            for s in st.session_state.scenes:
                if not (s.image_prompt or "").strip():
                    s.image_prompt = f"Create a cinematic historical visual for: {s.title}."

                s.image_variations = []
                for _ in range(int(st.session_state.variations_per_scene)):
                    updated = generate_image_for_scene(
                        s,
                        aspect_ratio=st.session_state.aspect_ratio,
                        visual_style=st.session_state.visual_style,
                    )
                    s.image_variations.append(updated.image_bytes)

                s.primary_image_index = 0
                s.image_bytes = s.image_variations[0] if s.image_variations else None
                if s.image_bytes:
                    _save_scene_image_bytes(s, s.image_bytes)

        st.toast("Image generation complete. Images auto-saved to assets/images.")
        st.rerun()

    st.divider()

    for s in st.session_state.scenes:
        with st.expander(f"{s.index:02d} — {s.title} images", expanded=False):
            if s.image_bytes:
                st.image(s.image_bytes, width="stretch")
            else:
                st.info("No primary image yet.")

            uploaded_scene_image = st.file_uploader(
                f"Upload your own image for scene {s.index:02d}",
                type=["png", "jpg", "jpeg"],
                key=f"scene_upload_{s.index}",
            )
            if uploaded_scene_image is not None:
                _save_scene_image_bytes(s, uploaded_scene_image.getvalue())
                st.success(f"Uploaded image applied to scene {s.index:02d}.")
                st.rerun()

            if len(s.image_variations) > 1:
                st.caption("Variations")
                for vi, b in enumerate(s.image_variations[1:], start=2):
                    if b:
                        st.image(b, caption=f"Variation {vi}", width="stretch")

            if s.image_error:
                st.error(s.image_error)

            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Regenerate this scene", key=f"regen_{s.index}", width="stretch"):
                    with st.spinner("Regenerating..."):
                        updated = generate_image_for_scene(
                            s,
                            aspect_ratio=st.session_state.aspect_ratio,
                            visual_style=st.session_state.visual_style,
                        )
                        s.image_bytes = updated.image_bytes
                        if s.image_variations:
                            s.image_variations[0] = updated.image_bytes
                        else:
                            s.image_variations = [updated.image_bytes]
                        if s.image_bytes:
                            _save_scene_image_bytes(s, s.image_bytes)
                    st.toast("Regenerated.")
                    st.rerun()
            with c2:
                st.caption("Edit the prompt in the Prompts tab for better results.")


def tab_voiceover() -> None:
    st.subheader("Voiceover (ElevenLabs)")

    if not script_ready():
        st.warning("Paste or generate a script first.")
        return

    st.session_state.voice_id = st.text_input(
        "ElevenLabs Voice ID",
        value=st.session_state.voice_id,
        placeholder="Paste your ElevenLabs voice_id here",
    )

    controls_left, controls_right = st.columns([1, 1])
    with controls_left:
        if st.button("Save voice ID", width="stretch"):
            try:
                _save_voice_id(st.session_state.voice_id)
            except OSError as exc:
                st.error(f"Could not save voice ID: {exc}")
            else:
                st.toast("Voice ID saved.")
    with controls_right:
        if st.button("Generate voiceover", type="primary", width="stretch"):
            try:
                _save_voice_id(st.session_state.voice_id)
            except OSError:
                pass

            with st.spinner("Generating voiceover..."):
                audio, err = generate_voiceover(
                    st.session_state.script_text,
                    voice_id=st.session_state.voice_id,
                    output_format="mp3",
                )
            st.session_state.voiceover_bytes = audio
            st.session_state.voiceover_error = err
            if err:
                st.error(err)
            else:
                project_folder = Path("data/projects") / _project_folder_name() / "assets/audio"
                project_folder.mkdir(parents=True, exist_ok=True)
                output_path = project_folder / "voiceover.mp3"
                output_path.write_bytes(audio)
                st.session_state.voiceover_saved_path = str(output_path)
                record_asset(_project_folder_name(), "voiceover", output_path)
                st.toast("Voiceover generated.")
            st.rerun()

    if st.session_state.voiceover_error:
        st.error(st.session_state.voiceover_error)

    if st.session_state.voiceover_bytes:
        st.audio(st.session_state.voiceover_bytes, format="audio/mp3")
        if st.session_state.voiceover_saved_path:
            st.caption(f"Saved to {st.session_state.voiceover_saved_path}")


def build_zip() -> bytes:
    """
    Export: script.txt, scenes.json, images/*.png, voiceover.mp3 (if present)
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("script.txt", st.session_state.script_text or "")

        scenes_meta = []
        for s in st.session_state.scenes:
            scenes_meta.append(
                {
                    "index": s.index,
                    "title": s.title,
                    "script_excerpt": s.script_excerpt,
                    "visual_intent": s.visual_intent,
                    "image_prompt": s.image_prompt,
                    "primary_image_index": s.primary_image_index,
                    "status": s.status,
                    "image_error": s.image_error,
                }
            )
        z.writestr("scenes.json", json.dumps(scenes_meta, indent=2))

        for s in st.session_state.scenes:
            if s.image_bytes:
                z.writestr(f"images/scene_{s.index:02d}.png", s.image_bytes)

        if st.session_state.voiceover_bytes:
            z.writestr("voiceover.mp3", st.session_state.voiceover_bytes)

    return buf.getvalue()


def tab_export() -> None:
    st.subheader("Export package")

    if not script_ready():
        st.warning("No script to export.")
        return
    if not scenes_ready():
        st.warning("No scenes to export.")
        return

    st.write(f"**Project:** {st.session_state.project_title}")
    st.write(f"**Scenes:** {len(st.session_state.scenes)}")
    st.write(f"**Images:** {sum(1 for s in st.session_state.scenes if s.image_bytes)}")
    st.write(f"**Voiceover:** {'Yes' if st.session_state.voiceover_bytes else 'No'}")

    zip_bytes = build_zip()
    st.download_button(
        "Download ZIP",
        data=zip_bytes,
        file_name=f"{st.session_state.project_title.replace(' ', '_')}.zip",
        mime="application/zip",
        width="stretch",
    )


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
                st.error(_openai_error_message(exc))
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
                st.error(_openai_error_message(exc))
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
                st.error(_openai_error_message(exc))
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
            project_folder = Path("data/projects") / _project_folder_name() / "assets/thumbnails"
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


def _tail_file(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return "".join(deque(handle, maxlen=lines))

def _tail_file(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return "".join(deque(handle, maxlen=lines))

def _tail_file(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return "".join(deque(handle, maxlen=lines))

def _load_timeline_meta(timeline_path: Path) -> dict:
    if not timeline_path.exists():
        return {}
    try:
        return json.loads(timeline_path.read_text(encoding="utf-8")).get("meta", {})
    except json.JSONDecodeError:
        return {}


def _caption_style_presets() -> dict[str, CaptionStyle]:
    return {
        "Bold Impact": CaptionStyle(font="Impact", font_size=10, line_spacing=6, bottom_margin=120),
        "Clean Sans": CaptionStyle(font="Arial", font_size=9, line_spacing=5, bottom_margin=120),
        "Tall Outline": CaptionStyle(font="Helvetica", font_size=10, line_spacing=6, bottom_margin=130),
        "Compact": CaptionStyle(font="Verdana", font_size=9, line_spacing=4, bottom_margin=110),
        "Large Center": CaptionStyle(font="Trebuchet MS", font_size=12, line_spacing=7, bottom_margin=140),
    }


def _caption_position_options() -> dict[str, str]:
    return {"Lower": "lower", "Center": "center", "Top": "top"}


def _apply_caption_preset(
    presets: dict[str, CaptionStyle],
    position_options: dict[str, str],
    style_key: str = "video_caption_style",
    font_key: str = "video_caption_font_size",
    position_key: str = "video_caption_position",
) -> None:
    selected_style = st.session_state.get(style_key)
    if not selected_style or selected_style not in presets:
        return
    preset = presets[selected_style]
    st.session_state[font_key] = preset.font_size
    label_for_position = {value: label for label, value in position_options.items()}
    st.session_state[position_key] = label_for_position.get(preset.position, "Lower")


def _match_caption_preset(style: CaptionStyle, presets: dict[str, CaptionStyle]) -> str:
    for name, preset in presets.items():
        if preset.model_dump(exclude={"position"}) == style.model_dump(exclude={"position"}):
            return name
    return next(iter(presets))


def _render_caption_preview(style: CaptionStyle) -> None:
    preview_font_size = max(12, int(style.font_size * 0.4))
    preview_line_height = preview_font_size + max(2, int(style.line_spacing * 0.4))
    preview_margin = max(12, int(style.bottom_margin * 0.3))
    if style.position == "top":
        position_css = f"top: {preview_margin}px;"
    elif style.position == "center":
        position_css = "top: 50%; transform: translateY(-50%);"
    else:
        position_css = f"bottom: {preview_margin}px;"
    preview_html = f"""
    <div style="width: 240px; height: 430px; background: #111; border-radius: 12px; position: relative; overflow: hidden; border: 1px solid #333;">
      <div style="position: absolute; inset: 0; background: linear-gradient(180deg, #222 0%, #111 60%);"></div>
      <div style="position: absolute; left: 12px; right: 12px; {position_css} text-align: center; color: #fff; font-family: '{style.font}', sans-serif; font-size: {preview_font_size}px; line-height: {preview_line_height}px; text-shadow: 0 2px 6px rgba(0,0,0,0.8);">
        The empires rise<br/>and fall
      </div>
    </div>
    """
    st.markdown(preview_html, unsafe_allow_html=True)


def _session_scene_images() -> list[tuple[int, bytes]]:
    scenes = st.session_state.get("scenes")
    if not scenes:
        return []
    session_images: list[tuple[int, bytes]] = []
    for scene in scenes:
        image_bytes = getattr(scene, "image_bytes", None)
        if image_bytes:
            session_images.append((scene.index, image_bytes))
    return session_images


def _sync_session_images(images_dir: Path, project_id: str) -> int:
    session_images = _session_scene_images()
    if not session_images:
        return 0
    images_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    for scene_index, image_bytes in session_images:
        destination = images_dir / f"s{scene_index:02d}.png"
        destination.write_bytes(image_bytes)
        saved_paths.append(destination)
    if saved_paths:
        record_assets(project_id, "image", saved_paths)
    return len(session_images)


def _scene_number_from_path(path: Path) -> int | None:
    match = re.search(r"s(\d+)", path.stem.lower())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _script_chunks_for_scene_count(script_text: str, scene_count: int) -> list[str]:
    text = (script_text or "").strip()
    if scene_count <= 0:
        return []
    if not text:
        return [""] * scene_count

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return [""] * scene_count

    chunks = [""] * scene_count
    for idx, sentence in enumerate(sentences):
        target = idx % scene_count
        chunks[target] = f"{chunks[target]} {sentence}".strip()
    return chunks


def _render_subtitle_preview(image_path: Path, subtitle: str) -> bytes:
    with Image.open(image_path) as image:
        canvas = image.convert("RGB")

    width, height = canvas.size
    overlay_height = max(80, int(height * 0.2))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rectangle([(0, height - overlay_height), (width, height)], fill=(0, 0, 0, 150))

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", max(18, int(height * 0.04)))
    except OSError:
        font = ImageFont.load_default()

    text = (subtitle or "").strip() or "(No subtitle)"
    draw.text((24, height - overlay_height + 18), text, fill=(255, 255, 255, 255), font=font)

    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _default_scene_captions(media_files: list[Path], timeline_path: Path) -> list[str]:
    caption_by_path: dict[str, str] = {}
    if timeline_path.exists():
        try:
            timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
        except ValueError:
            timeline = None
        if timeline:
            caption_by_path = {scene.image_path: scene.caption or "" for scene in timeline.scenes}

    script_excerpt_by_index: dict[int, str] = {}
    for scene in st.session_state.get("scenes", []):
        scene_index = getattr(scene, "index", None)
        excerpt = str(getattr(scene, "script_excerpt", "") or "").strip()
        if isinstance(scene_index, int) and excerpt:
            script_excerpt_by_index[scene_index] = excerpt

    script_chunks = _script_chunks_for_scene_count(st.session_state.get("script_text", ""), len(media_files))

    captions: list[str] = []
    for i, media_path in enumerate(media_files, start=1):
        from_timeline = caption_by_path.get(str(media_path), "").strip()
        if from_timeline:
            captions.append(from_timeline)
            continue
        scene_number = _scene_number_from_path(media_path) or i
        from_scene_excerpt = script_excerpt_by_index.get(scene_number, "").strip()
        if from_scene_excerpt:
            captions.append(from_scene_excerpt)
            continue
        captions.append(script_chunks[i - 1] if i - 1 < len(script_chunks) else "")
    return captions


def _collect_scene_captions(media_files: list[Path], timeline_path: Path) -> list[str]:
    state_key = f"video_scene_captions::{timeline_path}"
    if state_key not in st.session_state or len(st.session_state[state_key]) != len(media_files):
        st.session_state[state_key] = _default_scene_captions(media_files, timeline_path)

    captions: list[str] = st.session_state[state_key]
    for idx, media_path in enumerate(media_files, start=1):
        with st.expander(f"Scene {idx}: {media_path.name}"):
            if media_path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}:
                st.video(str(media_path))
                st.caption(f"Subtitle preview: {captions[idx - 1] or '(No subtitle)'}")
            else:
                st.image(_render_subtitle_preview(media_path, captions[idx - 1]), width="stretch")
            captions[idx - 1] = st.text_area(
                "Subtitle for this scene",
                value=captions[idx - 1],
                height=90,
                key=f"video_scene_caption_{idx}_{media_path.name}",
            )
    return captions


def _build_timeline_from_ui(
    project_name: str,
    title: str,
    images: list[Path],
    audio_files: list[Path],
    music_files: list[Path],
    aspect_ratio: str,
    fps: int,
    scene_duration: float | None,
    burn_captions: bool,
    caption_style: CaptionStyle,
    music_volume_db: float,
    include_voiceover: bool,
    include_music: bool,
    enable_motion: bool,
    crossfade: bool,
    crossfade_duration: float,
    scene_captions: list[str] | None = None,
) -> Timeline:
    voiceover_path = audio_files[0] if include_voiceover and audio_files else None
    timeline = build_default_timeline(
        project_id=project_name,
        title=title,
        images=images,
        voiceover_path=voiceover_path,
        aspect_ratio=aspect_ratio,
        fps=int(fps),
        scene_duration=scene_duration,
        burn_captions=burn_captions,
        caption_style=caption_style,
        music_path=music_files[0] if include_music and music_files else None,
        music_volume_db=music_volume_db,
        include_voiceover=include_voiceover,
        include_music=include_music,
        enable_motion=enable_motion,
        crossfade=crossfade,
        crossfade_duration=crossfade_duration,
    )
    if scene_captions:
        for scene, caption in zip(timeline.scenes, scene_captions):
            scene.caption = str(caption or "").strip() or None
    return timeline


def tab_video_compile() -> None:
    st.subheader("Video Studio")
    st.caption(
        "Video compile reads scene media from data/projects/<project_id>/assets/images and assets/videos, and audio from "
        "assets/audio and assets/music. To use generated images, export or save them into that folder first."
    )

    projects_root = Path("data/projects")
    project_dirs = sorted([p for p in projects_root.iterdir() if p.is_dir()])
    if not project_dirs:
        st.info("No projects found. Create a folder under data/projects/<project_id> to get started.")
        return

    project_name = st.selectbox("Project folder", [p.name for p in project_dirs])
    project_path = projects_root / project_name
    upsert_project(project_name, project_name.replace("_", " "))

    images_dir = project_path / "assets/images"
    videos_dir = project_path / "assets/videos"
    audio_dir = project_path / "assets/audio"
    music_dir = project_path / "assets/music"
    renders_dir = project_path / "renders"

    images = sorted([p for p in images_dir.glob("*.*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
    videos = sorted([p for p in videos_dir.glob("*.*") if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}])
    media_files = sorted(images + videos)
    audio_files = sorted([p for p in audio_dir.glob("*.*") if p.suffix.lower() in {".wav", ".mp3"}])
    music_files = sorted([p for p in music_dir.glob("*.*") if p.suffix.lower() in {".wav", ".mp3"}])
    if images:
        record_assets(project_name, "image", images)
    if videos:
        record_assets(project_name, "video", videos)
    if audio_files:
        record_assets(project_name, "voiceover", audio_files)
    if music_files:
        record_assets(project_name, "music", music_files)

    st.markdown("### Assets")
    cols = st.columns(3)
    cols[0].metric("Scene media", len(media_files))
    cols[1].metric("Voiceover files", len(audio_files))
    cols[2].metric("Music files", len(music_files))

    session_images = _session_scene_images()
    if session_images:
        st.caption(f"Generated images in session: {len(session_images)}")
        if st.button("Save generated images to assets/images", width="stretch", key="video_sync_images"):
            saved_count = _sync_session_images(images_dir, project_name)
            st.success(f"Saved {saved_count} generated image(s) to assets/images as s##.png.")
            st.rerun()
    else:
        st.caption("No generated images found in the current session.")

    if audio_files:
        st.caption(f"Using voiceover: {audio_files[0].name}")
    if music_files:
        st.caption(f"Using music bed: {music_files[0].name}")

    st.info(
        "Tip: Generate timeline.json or click Render to auto-build it. Toggle voiceover/music to render "
        "with or without audio tracks."
    )

    st.markdown("### Voiceover audio")
    if audio_files:
        audio_rows = [
            {"File": audio_file.name, "Size (MB)": f"{audio_file.stat().st_size / (1024 * 1024):.2f}"}
            for audio_file in audio_files
        ]
        st.dataframe(audio_rows, width="stretch", hide_index=True)
    else:
        st.info("No voiceover audio files found yet.")
        if st.session_state.voiceover_bytes:
            if st.button(
                "Save generated voiceover to assets/audio",
                width="stretch",
                key="video_save_generated_voiceover",
            ):
                audio_dir.mkdir(parents=True, exist_ok=True)
                destination = audio_dir / "voiceover.mp3"
                destination.write_bytes(st.session_state.voiceover_bytes)
                record_asset(project_name, "voiceover", destination)
                st.success("Saved generated voiceover to assets/audio/voiceover.mp3.")
                st.rerun()

    voiceover_upload = st.file_uploader(
        "Upload voiceover audio (.mp3 or .wav)",
        type=["mp3", "wav"],
        key="video_voiceover_upload",
    )
    if voiceover_upload is not None:
        audio_dir.mkdir(parents=True, exist_ok=True)
        destination = audio_dir / voiceover_upload.name
        destination.write_bytes(voiceover_upload.getbuffer())
        record_asset(project_name, "voiceover", destination)
        st.success(f"Saved {voiceover_upload.name} to assets/audio.")
        st.rerun()

    st.markdown("### Background music")
    if music_files:
        music_rows = [
            {"File": music_file.name, "Size (MB)": f"{music_file.stat().st_size / (1024 * 1024):.2f}"}
            for music_file in music_files
        ]
        st.dataframe(music_rows, width="stretch", hide_index=True)
    else:
        st.info("No background music files found yet.")

    upload_cols = st.columns([2, 1])
    with upload_cols[0]:
        uploaded_music = st.file_uploader(
            "Upload background music (.mp3 or .wav)",
            type=["mp3", "wav"],
            key="video_music_upload",
        )
        if uploaded_music is not None:
            music_dir.mkdir(parents=True, exist_ok=True)
            destination = music_dir / uploaded_music.name
            destination.write_bytes(uploaded_music.getbuffer())
            record_asset(project_name, "music", destination)
            st.success(f"Saved {uploaded_music.name} to assets/music.")
            st.rerun()
    with upload_cols[1]:
        music_url = st.text_input("Music URL", placeholder="https://example.com/track.mp3", key="video_music_url")
        if st.button("Add from URL", width="stretch", key="video_music_url_add"):
            if not music_url.strip():
                st.error("Enter a URL to fetch music.")
            else:
                parsed = urlparse(music_url)
                filename = Path(parsed.path).name
                if not filename:
                    st.error("URL does not include a filename.")
                elif Path(filename).suffix.lower() not in {".mp3", ".wav"}:
                    st.error("Only .mp3 or .wav files are supported.")
                else:
                    try:
                        with urlopen(music_url) as response:
                            music_bytes = response.read()
                    except Exception as exc:  # noqa: BLE001 - surface download errors to user
                        st.error(f"Failed to download music: {exc}")
                    else:
                        music_dir.mkdir(parents=True, exist_ok=True)
                        destination = music_dir / filename
                        destination.write_bytes(music_bytes)
                        record_asset(project_name, "music", destination)
                        st.success(f"Downloaded {filename} to assets/music.")
                        st.rerun()

    timeline_path = project_path / "timeline.json"
    meta_defaults = _load_timeline_meta(timeline_path)

    st.markdown("### Timeline settings")
    settings_cols = st.columns(3)
    with settings_cols[0]:
        title = st.text_input("Title", value=meta_defaults.get("title", project_name), key="video_title")
    with settings_cols[1]:
        aspect_ratio = st.selectbox(
            "Aspect ratio",
            ["9:16", "16:9"],
            index=0 if meta_defaults.get("aspect_ratio") != "16:9" else 1,
            key="video_aspect_ratio",
        )
    with settings_cols[2]:
        fps = st.number_input(
            "FPS",
            min_value=12,
            max_value=60,
            value=int(meta_defaults.get("fps", 30)),
            key="video_fps",
        )
    scene_duration_default = meta_defaults.get("scene_duration", 3.0)
    if scene_duration_default is None:
        scene_duration_default = 3.0
    scene_duration = st.slider(
        "Seconds per image",
        min_value=1.0,
        max_value=12.0,
        value=float(scene_duration_default),
        step=0.5,
        help="Used when building timelines. If voiceover is enabled, durations may drift from the audio length.",
        key="video_scene_duration",
    )

    st.markdown("### Scene subtitle review")
    if media_files:
        scene_captions = _collect_scene_captions(media_files, timeline_path)
        if st.button("Auto-fill subtitles from scene script excerpts", width="stretch", key="video_auto_captions"):
            scene_captions = _default_scene_captions(media_files, timeline_path)
            st.session_state[f"video_scene_captions::{timeline_path}"] = scene_captions
            st.success("Subtitles auto-filled from script and scene excerpts.")
            st.rerun()
    else:
        scene_captions = []
        st.info("Add scene images/videos to review subtitles per scene.")

    st.markdown("### Closed captions")
    captions_cols = st.columns([2, 1])
    with captions_cols[0]:
        burn_captions = st.checkbox(
            "Enable captions (burn-in)",
            value=bool(meta_defaults.get("burn_captions", True)),
            key="video_burn_captions",
        )
        caption_presets = _caption_style_presets()
        caption_style_defaults = meta_defaults.get("caption_style", {}) or {}
        try:
            current_caption_style = CaptionStyle(**caption_style_defaults)
        except (TypeError, ValueError):
            current_caption_style = CaptionStyle()
        position_options = _caption_position_options()
        caption_default_name = _match_caption_preset(current_caption_style, caption_presets)
        caption_style_name = st.selectbox(
            "Caption style",
            list(caption_presets.keys()),
            index=list(caption_presets.keys()).index(caption_default_name),
            key="video_caption_style",
            disabled=not burn_captions,
            on_change=_apply_caption_preset,
            args=(caption_presets, position_options),
        )
        current_position_label = next(
            (label for label, value in position_options.items() if value == current_caption_style.position),
            "Lower",
        )
        caption_position_label = st.selectbox(
            "Caption position",
            list(position_options.keys()),
            index=list(position_options.keys()).index(current_position_label),
            key="video_caption_position",
            disabled=not burn_captions,
        )
        selected_caption_style = caption_presets[caption_style_name].model_copy(deep=True)
        selected_caption_style.font_size = int(
            st.slider(
                "Caption size",
                min_value=12,
                max_value=48,
                value=selected_caption_style.font_size,
                step=2,
                key="video_caption_font_size",
                disabled=not burn_captions,
            )
        )
        selected_caption_style.position = position_options[caption_position_label]
    with captions_cols[1]:
        st.caption("Preview")
        _render_caption_preview(selected_caption_style)

    include_voiceover_default = meta_defaults.get("include_voiceover")
    if include_voiceover_default is None:
        include_voiceover_default = bool(audio_files)
    include_music_default = meta_defaults.get("include_music")
    if include_music_default is None:
        include_music_default = bool(music_files)

    include_voiceover_default = meta_defaults.get("include_voiceover")
    if include_voiceover_default is None:
        include_voiceover_default = bool(audio_files)
    include_music_default = meta_defaults.get("include_music")
    if include_music_default is None:
        include_music_default = bool(music_files)

    options_cols = st.columns(4)
    with options_cols[0]:
        include_voiceover = st.checkbox(
            "Include voiceover",
            value=bool(include_voiceover_default),
            key="video_include_voiceover",
        )
    with options_cols[1]:
        include_music = st.checkbox(
            "Include background music",
            value=bool(include_music_default),
            key="video_include_music",
        )
    with options_cols[2]:
        enable_motion = st.checkbox(
            "Ken Burns motion",
            value=True,
            key="video_enable_motion",
        )
    with options_cols[3]:
        crossfade = st.checkbox(
            "Crossfade scenes",
            value=bool(meta_defaults.get("crossfade", False)),
            key="video_crossfade",
        )

    effective_scene_duration = None if include_voiceover and audio_files else scene_duration

    crossfade_duration = st.slider(
        "Crossfade duration (seconds)",
        min_value=0.1,
        max_value=1.5,
        value=float(meta_defaults.get("crossfade_duration", 0.3)),
        step=0.1,
        key="video_crossfade_duration",
    )

    music_defaults = meta_defaults.get("music") or {}
    music_volume_db = st.slider(
        "Music volume (dB)",
        min_value=-36.0,
        max_value=0.0,
        value=float(music_defaults.get("volume_db", -18)),
        step=1.0,
        key="video_music_volume",
    )

    st.markdown("### Actions")
    if st.button("Generate timeline.json", width="stretch", key="video_generate_timeline"):
        if not media_files:
            st.error("No scene media found in assets/images or assets/videos. Add media to generate a timeline.")
        elif include_voiceover and not audio_files:
            st.error("Voiceover is enabled but no audio found in assets/audio/. Add a voiceover file first.")
        else:
            timeline = _build_timeline_from_ui(
                project_name=project_name,
                title=title,
                images=media_files,
                audio_files=audio_files,
                music_files=music_files,
                aspect_ratio=aspect_ratio,
                fps=int(fps),
                scene_duration=effective_scene_duration,
                burn_captions=burn_captions,
                caption_style=selected_caption_style,
                music_volume_db=music_volume_db,
                include_voiceover=include_voiceover,
                include_music=include_music,
                enable_motion=enable_motion,
                crossfade=crossfade,
                crossfade_duration=crossfade_duration,
                scene_captions=scene_captions,
            )
            write_timeline_json(timeline, timeline_path)
            st.success("timeline.json generated.")

    if st.button("Render video (FFmpeg)", width="stretch", key="video_render"):
        if not media_files:
            st.error("No scene media found in assets/images or assets/videos. Add media before rendering.")
            return
        if include_voiceover and not audio_files:
            st.error("Voiceover is enabled but no audio found in assets/audio/. Add a voiceover file first.")
            return

        if timeline_path.exists():
            try:
                timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                st.error(f"Unable to read timeline.json: {exc}")
                return
            image_paths = {str(image) for image in media_files}
            timeline_images = [scene.image_path for scene in timeline.scenes]
            if len(timeline_images) != len(media_files) or any(path not in image_paths for path in timeline_images):
                timeline = _build_timeline_from_ui(
                    project_name=project_name,
                    title=title,
                    images=media_files,
                    audio_files=audio_files,
                    music_files=music_files,
                    aspect_ratio=aspect_ratio,
                    fps=int(fps),
                    scene_duration=effective_scene_duration,
                    burn_captions=burn_captions,
                    caption_style=selected_caption_style,
                    music_volume_db=music_volume_db,
                    include_voiceover=include_voiceover,
                    include_music=include_music,
                    enable_motion=enable_motion,
                    crossfade=crossfade,
                    crossfade_duration=crossfade_duration,
                    scene_captions=scene_captions,
                )
                write_timeline_json(timeline, timeline_path)
                st.info("Timeline rebuilt to match current media.")
            else:
                timeline.meta.include_voiceover = include_voiceover
                timeline.meta.include_music = include_music
                if include_voiceover and audio_files:
                    timeline.meta.voiceover = timeline.meta.voiceover or Voiceover(path=str(audio_files[0]))
                    timeline.meta.voiceover.path = str(audio_files[0])
                else:
                    timeline.meta.voiceover = None
                if include_music and music_files:
                    timeline.meta.music = timeline.meta.music or Music(
                        path=str(music_files[0]),
                        volume_db=music_volume_db,
                    )
                    timeline.meta.music.path = str(music_files[0])
                    timeline.meta.music.volume_db = music_volume_db
                else:
                    timeline.meta.music = None
                timeline.meta.burn_captions = burn_captions
                timeline.meta.caption_style = selected_caption_style
                timeline.meta.scene_duration = effective_scene_duration
                for scene, caption in zip(timeline.scenes, scene_captions):
                    scene.caption = str(caption or "").strip() or None
                write_timeline_json(timeline, timeline_path)
        else:
            timeline = _build_timeline_from_ui(
                project_name=project_name,
                title=title,
                images=media_files,
                audio_files=audio_files,
                music_files=music_files,
                aspect_ratio=aspect_ratio,
                fps=int(fps),
                scene_duration=effective_scene_duration,
                burn_captions=burn_captions,
                caption_style=selected_caption_style,
                music_volume_db=music_volume_db,
                include_voiceover=include_voiceover,
                include_music=include_music,
                enable_motion=enable_motion,
                crossfade=crossfade,
                crossfade_duration=crossfade_duration,
                scene_captions=scene_captions,
            )
            write_timeline_json(timeline, timeline_path)

        missing_images = [scene.image_path for scene in timeline.scenes if not Path(scene.image_path).exists()]
        if missing_images:
            st.error("Missing scene media referenced by timeline.json.")
            st.code("\n".join(missing_images))
            return
        if timeline.meta.include_voiceover:
            if not timeline.meta.voiceover or not timeline.meta.voiceover.path:
                st.error("Voiceover is enabled but timeline.json has no voiceover path.")
                return
            if not Path(timeline.meta.voiceover.path).exists():
                st.error(f"Voiceover audio not found: {timeline.meta.voiceover.path}")
                return
        if timeline.meta.include_music and timeline.meta.music and timeline.meta.music.path:
            if not Path(timeline.meta.music.path).exists():
                st.error(f"Music file not found: {timeline.meta.music.path}")
                return

        try:
            ensure_ffmpeg_exists()
        except FFmpegNotFoundError as exc:
            st.error(str(exc))
        else:
            renders_dir.mkdir(parents=True, exist_ok=True)
            log_path = renders_dir / "render.log"
            with st.spinner("Rendering video with FFmpeg..."):
                try:
                    render_video_from_timeline(timeline_path, renders_dir / "final.mp4", log_path=log_path)
                except (RuntimeError, FileNotFoundError, ValueError) as exc:
                    st.error(f"Render failed: {exc}")
                else:
                    st.success("Render complete.")

    st.markdown("### Render output")
    video_path = renders_dir / "final.mp4"
    srt_path = renders_dir / "captions.srt"
    log_path = renders_dir / "render.log"

    if video_path.exists():
        st.video(str(video_path))
        with video_path.open("rb") as handle:
            st.download_button("Download video", handle, file_name="final.mp4")

    if srt_path.exists():
        st.download_button("Download captions.srt", srt_path.read_bytes(), file_name="captions.srt")

    log_text = _tail_file(log_path)
    if log_text:
        st.markdown("#### Render log")
        st.code(log_text, language="bash")


def main() -> None:
    st.set_page_config(page_title="The History Forge", layout="wide")
    require_passcode()
    init_state()
    upsert_project(_project_folder_name(), st.session_state.project_title)

    st.title("The History Forge")
    st.caption("Generate scripts, scene lists, prompts, images, and voiceover from a single workflow.")

    tabs = st.tabs(
        [
            "📝 Paste Script",
            "✨ Generate Script",
            "🧩 Scenes",
            "🧠 Prompts",
            "🖼️ Images",
            "🎙️ Voiceover",
            "📦 Export",
            "🎬 Video Studio",
            "🖼️ Title + Thumbnail",
        ]
    )

    with tabs[0]:
        tab_paste_script()
    with tabs[1]:
        tab_generate_script()
    with tabs[2]:
        tab_create_scenes()
    with tabs[3]:
        tab_create_prompts()
    with tabs[4]:
        tab_create_images()
    with tabs[5]:
        tab_voiceover()
    with tabs[6]:
        tab_export()
    with tabs[7]:
        tab_video_compile()
    with tabs[8]:
        tab_thumbnail_title()


if __name__ == "__main__":
    main()
