from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import streamlit as st

from src.storage import record_asset, record_assets, upsert_project
from src.video.ffmpeg_render import render_video_from_timeline
from src.video.timeline_builder import build_default_timeline, write_timeline_json
from src.video.timeline_schema import CaptionStyle, Music, Timeline, Voiceover
from src.video.utils import FFmpegNotFoundError, ensure_ffmpeg_exists


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


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
    scene_edits: list[dict[str, str | float]] | None = None,
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
    )
    if scene_edits:
        current_start = 0.0
        for scene, edit in zip(timeline.scenes, scene_edits):
            custom_duration = float(edit.get("duration", scene.duration))
            scene.duration = round(max(0.25, custom_duration), 3)
            scene.start = round(current_start, 3)
            scene.caption = str(edit.get("caption", "")).strip() or None
            current_start += scene.duration
        timeline.meta.scene_duration = None
    return timeline


def _default_scene_edits(media_files: list[Path], timeline_path: Path, default_duration: float) -> list[dict[str, str | float]]:
    timeline_scenes_by_path: dict[str, dict[str, str | float]] = {}
    if timeline_path.exists():
        try:
            timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
        except ValueError:
            timeline = None
        if timeline:
            timeline_scenes_by_path = {
                scene.image_path: {"duration": scene.duration, "caption": scene.caption or ""}
                for scene in timeline.scenes
            }

    edits: list[dict[str, str | float]] = []
    for media_path in media_files:
        from_timeline = timeline_scenes_by_path.get(str(media_path), {})
        edits.append(
            {
                "duration": float(from_timeline.get("duration", default_duration)),
                "caption": str(from_timeline.get("caption", "")),
            }
        )
    return edits


def _collect_scene_edits(media_files: list[Path], timeline_path: Path, default_duration: float) -> list[dict[str, str | float]]:
    state_key = f"video_scene_edits::{timeline_path}"
    if state_key not in st.session_state or len(st.session_state[state_key]) != len(media_files):
        st.session_state[state_key] = _default_scene_edits(media_files, timeline_path, default_duration)

    edits: list[dict[str, str | float]] = st.session_state[state_key]
    for index, media_path in enumerate(media_files, start=1):
        row = edits[index - 1]
        with st.expander(f"Scene {index}: {media_path.name}"):
            col_a, col_b = st.columns([1, 2])
            with col_a:
                if _is_video(media_path):
                    st.video(str(media_path))
                else:
                    st.image(str(media_path), width=220)
            with col_b:
                row["duration"] = st.number_input(
                    "Duration (seconds)",
                    min_value=0.25,
                    max_value=30.0,
                    step=0.25,
                    value=float(row.get("duration", default_duration)),
                    key=f"video_scene_duration_{index}_{media_path.name}",
                )
                row["caption"] = st.text_area(
                    "Subtitle line(s) for this scene",
                    value=str(row.get("caption", "")),
                    height=80,
                    key=f"video_scene_caption_{index}_{media_path.name}",
                    placeholder="Optional subtitle text shown during this scene.",
                )
    return edits


st.set_page_config(page_title="Video Studio", layout="wide")

st.title("🎬 Video Studio")

projects_root = Path("data/projects")
project_dirs = sorted([p for p in projects_root.iterdir() if p.is_dir()])
if not project_dirs:
    st.info("No projects found. Create a folder under data/projects/<project_id> to get started.")
    st.stop()

project_name = st.selectbox("Project folder", [p.name for p in project_dirs])
project_path = projects_root / project_name
upsert_project(project_name, project_name.replace("_", " "))

images_dir = project_path / "assets/images"
videos_dir = project_path / "assets/videos"
audio_dir = project_path / "assets/audio"
music_dir = project_path / "assets/music"
renders_dir = project_path / "renders"

images = sorted([p for p in images_dir.glob("*.*") if p.suffix.lower() in IMAGE_EXTENSIONS])
videos = sorted([p for p in videos_dir.glob("*.*") if p.suffix.lower() in VIDEO_EXTENSIONS])
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

uploaded_scene_media = st.file_uploader(
    "Upload scene media (.png, .jpg, .jpeg, .mp4, .mov, .webm, .mkv)",
    type=["png", "jpg", "jpeg", "mp4", "mov", "webm", "mkv"],
    key="video_scene_media_upload",
)
if uploaded_scene_media is not None:
    suffix = Path(uploaded_scene_media.name).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        destination_dir = videos_dir
        asset_type = "video"
    else:
        destination_dir = images_dir
        asset_type = "image"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / uploaded_scene_media.name
    destination.write_bytes(uploaded_scene_media.getbuffer())
    record_asset(project_name, asset_type, destination)
    st.success(f"Saved {uploaded_scene_media.name} to {destination_dir.relative_to(project_path)}.")
    st.rerun()

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
    title = st.text_input("Title", value=meta_defaults.get("title", project_name))
with settings_cols[1]:
    aspect_ratio = st.selectbox("Aspect ratio", ["9:16", "16:9"], index=0 if meta_defaults.get("aspect_ratio") != "16:9" else 1)
with settings_cols[2]:
    fps = st.number_input("FPS", min_value=12, max_value=60, value=int(meta_defaults.get("fps", 30)))

scene_duration_default = meta_defaults.get("scene_duration", 3.0)
if scene_duration_default is None:
    scene_duration_default = 3.0
scene_duration = st.slider(
    "Default seconds per scene",
    min_value=1.0,
    max_value=12.0,
    value=float(scene_duration_default),
    step=0.5,
    help="Default used for new scene rows. You can override each scene duration in the Scene editor tab.",
    key="video_scene_duration",
)

edit_tab, render_tab = st.tabs(["Scene editor", "Render settings"])

with edit_tab:
    st.caption("Set per-scene timing and subtitles. These values are written into timeline.json.")
    if media_files:
        scene_edits = _collect_scene_edits(media_files, timeline_path, float(scene_duration))
    else:
        scene_edits = []
        st.info("No media found in assets/images or assets/videos. Add media to edit scene timing and subtitles.")

with render_tab:
    st.caption("Global render settings and caption style controls.")

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

    music_defaults = meta_defaults.get("music") or {}
    music_volume_db = st.slider(
        "Music volume (dB)",
        min_value=-36.0,
        max_value=0.0,
        value=float(music_defaults.get("volume_db", -18)),
        step=1.0,
    )

if burn_captions and not any(str(edit.get("caption", "")).strip() for edit in scene_edits):
    st.warning("Captions are enabled, but no per-scene subtitle text is set yet. Add lines in the Scene editor tab.")

st.markdown("### Actions")

if st.button("Generate timeline.json", width="stretch"):
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
            scene_duration=float(scene_duration),
            burn_captions=burn_captions,
            caption_style=selected_caption_style,
            music_volume_db=music_volume_db,
            include_voiceover=include_voiceover,
            include_music=include_music,
            scene_edits=scene_edits,
        )
        write_timeline_json(timeline, timeline_path)
        st.success("timeline.json generated.")

if st.button("Render video (FFmpeg)", width="stretch"):
    if not media_files:
        st.error("No scene media found in assets/images or assets/videos. Add media before rendering.")
        st.stop()
    if include_voiceover and not audio_files:
        st.error("Voiceover is enabled but no audio found in assets/audio/. Add a voiceover file first.")
        st.stop()

    timeline = _build_timeline_from_ui(
        project_name=project_name,
        title=title,
        images=media_files,
        audio_files=audio_files,
        music_files=music_files,
        aspect_ratio=aspect_ratio,
        fps=int(fps),
        scene_duration=float(scene_duration),
        burn_captions=burn_captions,
        caption_style=selected_caption_style,
        music_volume_db=music_volume_db,
        include_voiceover=include_voiceover,
        include_music=include_music,
        scene_edits=scene_edits,
    )
    if include_voiceover and audio_files:
        timeline.meta.voiceover = Voiceover(path=str(audio_files[0]))
    else:
        timeline.meta.voiceover = None
    if include_music and music_files:
        timeline.meta.music = Music(path=str(music_files[0]), volume_db=music_volume_db)
    else:
        timeline.meta.music = None
    write_timeline_json(timeline, timeline_path)

    missing_images = [scene.image_path for scene in timeline.scenes if not Path(scene.image_path).exists()]
    if missing_images:
        st.error("Missing scene media referenced by timeline.json.")
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
