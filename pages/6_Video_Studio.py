from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import streamlit as st

from src.video.ffmpeg_render import render_video_from_timeline
from src.video.timeline_builder import build_default_timeline, write_timeline_json
from src.video.timeline_schema import CaptionStyle, Music, Timeline, Voiceover
from src.video.utils import FFmpegNotFoundError, ensure_ffmpeg_exists


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


def _sync_session_images(images_dir: Path) -> int:
    session_images = _session_scene_images()
    if not session_images:
        return 0
    images_dir.mkdir(parents=True, exist_ok=True)
    for scene_index, image_bytes in session_images:
        destination = images_dir / f"s{scene_index:02d}.png"
        destination.write_bytes(image_bytes)
    return len(session_images)


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
) -> Timeline:
    voiceover_path = audio_files[0] if include_voiceover and audio_files else None
    return build_default_timeline(
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
    )


st.set_page_config(page_title="Video Studio", layout="wide")

st.title("🎬 Video Studio")

projects_root = Path("data/projects")
project_dirs = sorted([p for p in projects_root.iterdir() if p.is_dir()])
if not project_dirs:
    st.info("No projects found. Create a folder under data/projects/<project_id> to get started.")
    st.stop()

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
    if st.button("Save generated images to assets/images", width="stretch", key="video_sync_images"):
        saved_count = _sync_session_images(images_dir)
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

if st.session_state.get("voiceover_bytes"):
    if st.button("Save generated voiceover to assets/audio", width="stretch", key="video_save_generated_voiceover"):
        audio_dir.mkdir(parents=True, exist_ok=True)
        destination = audio_dir / "voiceover.mp3"
        destination.write_bytes(st.session_state.voiceover_bytes)
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
                    st.success(f"Downloaded {filename} to assets/music.")
                    st.rerun()

timeline_path = project_path / "timeline.json"
meta_defaults = _load_timeline_meta(timeline_path)

st.markdown("### Timeline settings")
settings_cols = st.columns(3)
with settings_cols[0]:
    title = st.text_input("Title", value=meta_defaults.get("title", project_name))
with settings_cols[1]:
    aspect_ratio = st.selectbox("Aspect ratio", ["9:16", "16:9"], index=0 if meta_defaults.get("aspect_ratio") != "16:9" else 1)
with settings_cols[2]:
    fps = st.number_input("FPS", min_value=12, max_value=60, value=int(meta_defaults.get("fps", 30)))

scene_duration = st.slider(
    "Seconds per image",
    min_value=1.0,
    max_value=12.0,
    value=float(meta_defaults.get("scene_duration", 3.0)),
    step=0.5,
    help="Used when building timelines. If voiceover is enabled, durations may drift from the audio length.",
    key="video_scene_duration",
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

options_cols = st.columns(2)
with options_cols[0]:
    include_voiceover = st.checkbox("Include voiceover", value=bool(include_voiceover_default))
with options_cols[1]:
    include_music = st.checkbox("Include background music", value=bool(include_music_default))

include_voiceover_default = meta_defaults.get("include_voiceover")
if include_voiceover_default is None:
    include_voiceover_default = bool(audio_files)
include_music_default = meta_defaults.get("include_music")
if include_music_default is None:
    include_music_default = bool(music_files)

options_cols = st.columns(2)
with options_cols[0]:
    include_voiceover = st.checkbox("Include voiceover", value=bool(include_voiceover_default))
with options_cols[1]:
    include_music = st.checkbox("Include background music", value=bool(include_music_default))

effective_scene_duration = None if include_voiceover and audio_files else scene_duration

music_defaults = meta_defaults.get("music") or {}
music_volume_db = st.slider(
    "Music volume (dB)",
    min_value=-36.0,
    max_value=0.0,
    value=float(music_defaults.get("volume_db", -18)),
    step=1.0,
)

st.markdown("### Actions")

if st.button("Generate timeline.json", width="stretch"):
    if not images:
        st.error("No images found in assets/images/. Add scene images to generate a timeline.")
    elif include_voiceover and not audio_files:
        st.error("Voiceover is enabled but no audio found in assets/audio/. Add a voiceover file first.")
    else:
        timeline = _build_timeline_from_ui(
            project_name=project_name,
            title=title,
            images=images,
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
        )
        write_timeline_json(timeline, timeline_path)
        st.success("timeline.json generated.")

if st.button("Render video (FFmpeg)", width="stretch"):
    if not images:
        st.error("No images found in assets/images/. Add scene images before rendering.")
        st.stop()
    if include_voiceover and not audio_files:
        st.error("Voiceover is enabled but no audio found in assets/audio/. Add a voiceover file first.")
        st.stop()
    if timeline_path.exists():
        try:
            timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            st.error(f"Unable to read timeline.json: {exc}")
            st.stop()
        image_paths = {str(image) for image in images}
        timeline_images = [scene.image_path for scene in timeline.scenes]
        if len(timeline_images) != len(images) or any(path not in image_paths for path in timeline_images):
            timeline = _build_timeline_from_ui(
                project_name=project_name,
                title=title,
                images=images,
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
            )
            write_timeline_json(timeline, timeline_path)
            st.info("Timeline rebuilt to match current images.")
        else:
            timeline.meta.include_voiceover = include_voiceover
            timeline.meta.include_music = include_music
            if include_voiceover and audio_files:
                timeline.meta.voiceover = timeline.meta.voiceover or Voiceover(path=str(audio_files[0]))
                timeline.meta.voiceover.path = str(audio_files[0])
            else:
                timeline.meta.voiceover = None
            if include_music and music_files:
                timeline.meta.music = timeline.meta.music or Music(path=str(music_files[0]), volume_db=music_volume_db)
                timeline.meta.music.path = str(music_files[0])
                timeline.meta.music.volume_db = music_volume_db
            else:
                timeline.meta.music = None
            timeline.meta.burn_captions = burn_captions
            timeline.meta.caption_style = selected_caption_style
            timeline.meta.scene_duration = effective_scene_duration
            write_timeline_json(timeline, timeline_path)
    else:
        timeline = _build_timeline_from_ui(
            project_name=project_name,
            title=title,
            images=images,
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
        )
        write_timeline_json(timeline, timeline_path)

    missing_images = [scene.image_path for scene in timeline.scenes if not Path(scene.image_path).exists()]
    if missing_images:
        st.error("Missing scene images referenced by timeline.json.")
        st.code("\n".join(missing_images))
        st.stop()
    if timeline.meta.include_voiceover:
        if not timeline.meta.voiceover or not timeline.meta.voiceover.path:
            st.error("Voiceover is enabled but timeline.json has no voiceover path.")
            st.stop()
        if not Path(timeline.meta.voiceover.path).exists():
            st.error(f"Voiceover audio not found: {timeline.meta.voiceover.path}")
            st.stop()
    if timeline.meta.include_music and timeline.meta.music and timeline.meta.music.path:
        if not Path(timeline.meta.music.path).exists():
            st.error(f"Music file not found: {timeline.meta.music.path}")
            st.stop()

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
