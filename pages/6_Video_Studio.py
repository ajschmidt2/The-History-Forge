from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import streamlit as st

from src.video.ffmpeg_render import render_video_from_timeline
from src.video.timeline_builder import build_default_timeline, write_timeline_json
from src.video.timeline_schema import Timeline
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

if audio_files:
    st.caption(f"Using voiceover: {audio_files[0].name}")
if music_files:
    st.caption(f"Using music bed: {music_files[0].name}")

timeline_path = project_path / "timeline.json"
meta_defaults = _load_timeline_meta(timeline_path)

st.markdown("### Timeline settings")
settings_cols = st.columns(4)
with settings_cols[0]:
    title = st.text_input("Title", value=meta_defaults.get("title", project_name))
with settings_cols[1]:
    aspect_ratio = st.selectbox("Aspect ratio", ["9:16", "16:9"], index=0 if meta_defaults.get("aspect_ratio") != "16:9" else 1)
with settings_cols[2]:
    fps = st.number_input("FPS", min_value=12, max_value=60, value=int(meta_defaults.get("fps", 30)))
with settings_cols[3]:
    burn_captions = st.checkbox("Burn captions", value=bool(meta_defaults.get("burn_captions", True)))

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

music_volume_db = st.slider(
    "Music volume (dB)",
    min_value=-36.0,
    max_value=0.0,
    value=float(meta_defaults.get("music", {}).get("volume_db", -18)),
    step=1.0,
)

st.markdown("### Actions")

if st.button("Generate timeline.json", use_container_width=True):
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
            music_path=music_files[0] if include_music and music_files else None,
            music_volume_db=music_volume_db,
            include_voiceover=include_voiceover,
            include_music=include_music,
        )
        write_timeline_json(timeline, timeline_path)
        st.success("timeline.json generated.")

render_disabled = not timeline_path.exists()
if st.button("Render video (FFmpeg)", use_container_width=True, disabled=render_disabled):
    try:
        timeline = Timeline.parse_file(timeline_path)
    except ValueError as exc:
        st.error(f"Unable to read timeline.json: {exc}")
        st.stop()

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
