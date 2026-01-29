import json
import zipfile
from collections import deque
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import streamlit as st

from utils import (
    Scene,
    generate_lucky_topic,
    generate_script,
    split_script_into_scenes,
    generate_prompts_for_scenes,
    generate_image_for_scene,
    generate_voiceover,
)
from src.video.ffmpeg_render import render_video_from_timeline
from src.video.timeline_builder import build_default_timeline, write_timeline_json
from src.video.timeline_schema import CaptionStyle, Timeline
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

    st.session_state.setdefault("voice_id", "")
    st.session_state.setdefault("voiceover_bytes", None)
    st.session_state.setdefault("voiceover_error", None)


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
                    s.primary_image_index = 0
                    s.image_error = ""

    if after in ("script", "scenes", "prompts"):
        if isinstance(st.session_state.scenes, list):
            for s in st.session_state.scenes:
                if isinstance(s, Scene):
                    s.image_bytes = None
                    s.image_variations = []
                    s.primary_image_index = 0
                    s.image_error = ""


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

    if st.button("Use this script →", type="primary", use_container_width=True):
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
        if st.button("🎲 I'm Feeling Lucky", use_container_width=True):
            st.session_state.topic = generate_lucky_topic()
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

    if st.button("Generate Script", type="primary", use_container_width=True):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic or use I'm Feeling Lucky.")
            return
        with st.spinner("Generating script..."):
            generated_script = generate_script(
                topic=st.session_state.topic,
                length=st.session_state.length,
                tone=st.session_state.tone,
            )
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

    if st.button("Split script into scenes", type="primary", use_container_width=True):
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

    if st.button("Generate prompts for all scenes", type="primary", use_container_width=True):
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


def tab_create_images() -> None:
    st.subheader("Create images")

    if not scenes_ready():
        st.warning("Create scenes first.")
        return

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

    if st.button("Generate images for all scenes", type="primary", use_container_width=True):
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

        st.toast("Image generation complete.")
        st.rerun()

    st.divider()

    for s in st.session_state.scenes:
        with st.expander(f"{s.index:02d} — {s.title} images", expanded=False):
            if s.image_bytes:
                st.image(s.image_bytes, use_container_width=True)
            else:
                st.info("No primary image yet.")

            if len(s.image_variations) > 1:
                st.caption("Variations")
                for vi, b in enumerate(s.image_variations[1:], start=2):
                    if b:
                        st.image(b, caption=f"Variation {vi}", use_container_width=True)

            if s.image_error:
                st.error(s.image_error)

            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Regenerate this scene", key=f"regen_{s.index}", use_container_width=True):
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

    if st.button("Generate voiceover", type="primary", use_container_width=True):
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
            st.toast("Voiceover generated.")
        st.rerun()

    if st.session_state.voiceover_error:
        st.error(st.session_state.voiceover_error)

    if st.session_state.voiceover_bytes:
        st.audio(st.session_state.voiceover_bytes, format="audio/mp3")


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
        use_container_width=True,
    )


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
        "Bold Impact": CaptionStyle(font="Impact", font_size=72, line_spacing=10, bottom_margin=130),
        "Clean Sans": CaptionStyle(font="Arial", font_size=60, line_spacing=8, bottom_margin=140),
        "Tall Outline": CaptionStyle(font="Helvetica", font_size=68, line_spacing=10, bottom_margin=150),
        "Compact": CaptionStyle(font="Verdana", font_size=54, line_spacing=6, bottom_margin=120),
        "Large Center": CaptionStyle(font="Trebuchet MS", font_size=80, line_spacing=12, bottom_margin=160),
    }


def _caption_position_options() -> dict[str, str]:
    return {"Lower": "lower", "Center": "center", "Top": "top"}


def _match_caption_preset(style: CaptionStyle, presets: dict[str, CaptionStyle]) -> str:
    for name, preset in presets.items():
        if preset.dict(exclude={"position"}) == style.dict(exclude={"position"}):
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


def _sync_session_images(images_dir: Path) -> int:
    session_images = _session_scene_images()
    if not session_images:
        return 0
    images_dir.mkdir(parents=True, exist_ok=True)
    for scene_index, image_bytes in session_images:
        destination = images_dir / f"s{scene_index:02d}.png"
        destination.write_bytes(image_bytes)
    return len(session_images)


def tab_video_compile() -> None:
    st.subheader("Video Studio")
    st.caption(
        "Video compile reads scene images from data/projects/<project_id>/assets/images and audio from "
        "assets/audio and assets/music. To use generated images, export or save them into that folder first."
    )

    projects_root = Path("data/projects")
    project_dirs = sorted([p for p in projects_root.iterdir() if p.is_dir()])
    if not project_dirs:
        st.info("No projects found. Create a folder under data/projects/<project_id> to get started.")
        return

    project_name = st.selectbox("Project folder", [p.name for p in project_dirs])
    project_path = projects_root / project_name

    images_dir = project_path / "assets/images"
    audio_dir = project_path / "assets/audio"
    music_dir = project_path / "assets/music"
    renders_dir = project_path / "renders"

    images = sorted([p for p in images_dir.glob("*.*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
    audio_files = sorted([p for p in audio_dir.glob("*.*") if p.suffix.lower() in {".wav", ".mp3"}])
    music_files = sorted([p for p in music_dir.glob("*.*") if p.suffix.lower() in {".wav", ".mp3"}])

    st.markdown("### Assets")
    cols = st.columns(3)
    cols[0].metric("Images", len(images))
    cols[1].metric("Voiceover files", len(audio_files))
    cols[2].metric("Music files", len(music_files))

    session_images = _session_scene_images()
    if session_images:
        st.caption(f"Generated images in session: {len(session_images)}")
        if st.button("Save generated images to assets/images", use_container_width=True, key="video_sync_images"):
            saved_count = _sync_session_images(images_dir)
            st.success(f"Saved {saved_count} generated image(s) to assets/images as s##.png.")
            st.rerun()
    else:
        st.caption("No generated images found in the current session.")

    if audio_files:
        st.caption(f"Using voiceover: {audio_files[0].name}")
    if music_files:
        st.caption(f"Using music bed: {music_files[0].name}")

    st.markdown("### Background music")
    if music_files:
        music_rows = [
            {"File": music_file.name, "Size (MB)": f"{music_file.stat().st_size / (1024 * 1024):.2f}"}
            for music_file in music_files
        ]
        st.dataframe(music_rows, use_container_width=True, hide_index=True)
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
            st.success(f"Saved {uploaded_music.name} to assets/music.")
            st.rerun()
    with upload_cols[1]:
        music_url = st.text_input("Music URL", placeholder="https://example.com/track.mp3", key="video_music_url")
        if st.button("Add from URL", use_container_width=True, key="video_music_url_add"):
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
        caption_default_name = _match_caption_preset(current_caption_style, caption_presets)
        caption_style_name = st.selectbox(
            "Caption style",
            list(caption_presets.keys()),
            index=list(caption_presets.keys()).index(caption_default_name),
            key="video_caption_style",
            disabled=not burn_captions,
        )
        position_options = _caption_position_options()
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
        selected_caption_style = caption_presets[caption_style_name].copy(deep=True)
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

    crossfade_duration = st.slider(
        "Crossfade duration (seconds)",
        min_value=0.1,
        max_value=1.5,
        value=float(meta_defaults.get("crossfade_duration", 0.3)),
        step=0.1,
        key="video_crossfade_duration",
    )

    music_volume_db = st.slider(
        "Music volume (dB)",
        min_value=-36.0,
        max_value=0.0,
        value=float(meta_defaults.get("music", {}).get("volume_db", -18)),
        step=1.0,
        key="video_music_volume",
    )

    st.markdown("### Actions")
    if st.button("Generate timeline.json", use_container_width=True, key="video_generate_timeline"):
        if not images:
            st.error("No images found in assets/images/. Add scene images to generate a timeline.")
        elif include_voiceover and not audio_files:
            st.error("Voiceover is enabled but no audio found in assets/audio/. Add a voiceover file first.")
        else:
            voiceover_path = audio_files[0] if include_voiceover and audio_files else None
            timeline = build_default_timeline(
                project_id=project_name,
                title=title,
                images=images,
                voiceover_path=voiceover_path,
                aspect_ratio=aspect_ratio,
                fps=int(fps),
                burn_captions=burn_captions,
                caption_style=selected_caption_style,
                music_path=music_files[0] if include_music and music_files else None,
                music_volume_db=music_volume_db,
                include_voiceover=include_voiceover,
                include_music=include_music,
                enable_motion=enable_motion,
                crossfade=crossfade,
                crossfade_duration=crossfade_duration,
            )
            write_timeline_json(timeline, timeline_path)
            st.success("timeline.json generated.")

    render_disabled = not timeline_path.exists()
    if st.button("Render video (FFmpeg)", use_container_width=True, disabled=render_disabled, key="video_render"):
        try:
            timeline = Timeline.parse_file(timeline_path)
        except ValueError as exc:
            st.error(f"Unable to read timeline.json: {exc}")
            return

        missing_images = [scene.image_path for scene in timeline.scenes if not Path(scene.image_path).exists()]
        if missing_images:
            st.error("Missing scene images referenced by timeline.json.")
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
                render_video_from_timeline(timeline_path, renders_dir / "final.mp4", log_path=log_path)
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
            "🎞️ Video Compile",
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


if __name__ == "__main__":
    main()
