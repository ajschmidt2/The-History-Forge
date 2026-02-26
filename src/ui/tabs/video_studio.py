import json
import os
import re
import shutil
import subprocess
import traceback
from collections import deque
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import streamlit as st
from PIL import Image, ImageDraw, ImageFont

from src.storage import record_asset, record_assets, upsert_project
import src.supabase_storage as _sb_store
from src.video.ffmpeg_render import render_video_from_timeline
from src.video.timeline_schema import CaptionStyle, Timeline
from src.video.utils import FFmpegNotFoundError, ensure_ffmpeg_exists, get_ffmpeg_exe
from src.ui.state import active_project_id
from src.ui.timeline_sync import sync_timeline_for_project
from src.ui.caption_format import format_caption

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


def _load_render_report(report_path: Path) -> dict:
    if not report_path.exists():
        return {}
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _render_error_output_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)




def _compute_render_timeout(duration_seconds: float, user_timeout_seconds: int, allow_long_timeout: bool) -> int:
    adaptive_timeout = max(int(user_timeout_seconds), int(60 + (duration_seconds * 12)))
    if allow_long_timeout:
        return adaptive_timeout
    return min(2700, adaptive_timeout)


def _validate_render_preflight(
    timeline: Timeline,
    media_files: list[Path],
    output_path: Path,
    allow_silent_build: bool,
) -> bool:
    missing_or_empty_media: list[str] = []
    for scene in timeline.scenes:
        scene_path = Path(scene.image_path)
        if not scene_path.exists() or scene_path.stat().st_size <= 0:
            missing_or_empty_media.append(str(scene_path))

    if missing_or_empty_media:
        st.error("Missing or empty scene media files referenced by timeline.json.")
        st.code("\n".join(missing_or_empty_media))
        return False

    if timeline.meta.include_voiceover and not allow_silent_build:
        if not timeline.meta.voiceover or not timeline.meta.voiceover.path:
            st.error("Voiceover is enabled but timeline.json has no voiceover path.")
            return False
        voiceover_path = Path(timeline.meta.voiceover.path)
        if not voiceover_path.exists() or voiceover_path.stat().st_size <= 0:
            st.error(f"Voiceover audio not found or empty: {timeline.meta.voiceover.path}")
            return False

    if timeline.meta.include_music and timeline.meta.music and timeline.meta.music.path:
        music_path = Path(timeline.meta.music.path)
        if not music_path.exists() or music_path.stat().st_size <= 0:
            st.error(f"Music file not found or empty: {timeline.meta.music.path}")
            return False

    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.exists() or not output_dir.is_dir():
        st.error(f"Render output directory is unavailable: {output_dir}")
        return False
    if not os.access(output_dir, os.W_OK):
        st.error(f"Render output directory is not writable: {output_dir}")
        return False

    expected_paths = {str(path) for path in media_files}
    timeline_paths = [scene.image_path for scene in timeline.scenes]
    if len(timeline_paths) != len(media_files) or any(path not in expected_paths for path in timeline_paths):
        st.error("timeline.json is out of sync with project media. Click Generate timeline.json to resync.")
        return False

    return True

def _trim_debug_text(value: str, limit: int = 140) -> str:
    normalized = (value or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _render_debug_samples(items: list[str], label: str) -> None:
    st.markdown(f"**{label}**")
    if not items:
        st.caption("(none)")
        return

    st.caption("First 3")
    for idx, item in enumerate(items[:3]):
        st.code(f"[{idx}] repr={item!r}\ntrimmed={_trim_debug_text(item)!r}")

    st.caption("Last 3")
    start_idx = max(0, len(items) - 3)
    for idx in range(start_idx, len(items)):
        item = items[idx]
        st.code(f"[{idx}] repr={item!r}\ntrimmed={_trim_debug_text(item)!r}")

def _caption_style_presets() -> dict[str, CaptionStyle]:
    return {
        "Bold Impact": CaptionStyle(font="Impact", font_size=56, line_spacing=6, bottom_margin=120),
        "Clean Sans": CaptionStyle(font="Arial", font_size=48, line_spacing=5, bottom_margin=120),
        "Tall Outline": CaptionStyle(font="Helvetica", font_size=52, line_spacing=6, bottom_margin=130),
        "Compact": CaptionStyle(font="Verdana", font_size=42, line_spacing=4, bottom_margin=110),
        "Large Center": CaptionStyle(font="Trebuchet MS", font_size=64, line_spacing=7, bottom_margin=140),
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


def _preview_caption_style(meta_defaults: dict) -> tuple[bool, CaptionStyle]:
    burn_default = bool(meta_defaults.get("burn_captions", True))
    burn_captions = bool(st.session_state.get("video_burn_captions", burn_default))

    caption_style_defaults = meta_defaults.get("caption_style", {}) or {}
    try:
        style = CaptionStyle(**caption_style_defaults)
    except (TypeError, ValueError):
        style = CaptionStyle()

    presets = _caption_style_presets()
    selected_preset_name = st.session_state.get("video_caption_style")
    if isinstance(selected_preset_name, str) and selected_preset_name in presets:
        style = presets[selected_preset_name].model_copy(deep=True)

    style.font_size = int(st.session_state.get("video_caption_font_size", style.font_size))

    position_options = _caption_position_options()
    selected_label = st.session_state.get("video_caption_position")
    if isinstance(selected_label, str) and selected_label in position_options:
        style.position = position_options[selected_label]

    return burn_captions, style


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


def _media_sort_key(path: Path) -> tuple[int, int, str]:
    scene_number = _scene_number_from_path(path)
    if scene_number is not None:
        return (0, scene_number, path.name.lower())
    return (1, 10**9, path.name.lower())


def _normalize_caption_list(captions: list[str], expected_count: int) -> list[str]:
    normalized = [str(caption or "") for caption in captions[:expected_count]]
    if len(normalized) < expected_count:
        normalized.extend([""] * (expected_count - len(normalized)))
    return normalized


def _caption_wrap_settings(aspect_ratio: str, font_size: int) -> tuple[int, int]:
    safe_font = max(18, int(font_size or 48))
    if str(aspect_ratio or "9:16") == "9:16":
        usable_width_px = 1080 - 160
        chars = max(12, min(28, int(usable_width_px / max(8.0, safe_font * 0.58))))
        return (14, chars)
    usable_width_px = 1920 - 200
    chars = max(22, min(52, int(usable_width_px / max(8.0, safe_font * 0.55))))
    return (12, chars)


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


def _render_subtitle_preview(
    image_path: Path,
    subtitle: str,
    *,
    caption_style: CaptionStyle,
    burn_captions: bool,
) -> bytes:
    with Image.open(image_path) as image:
        canvas = image.convert("RGB")

    if not burn_captions:
        buffer = BytesIO()
        canvas.save(buffer, format="PNG")
        return buffer.getvalue()

    width, height = canvas.size
    draw = ImageDraw.Draw(canvas, "RGBA")

    font_size = max(18, int(caption_style.font_size * (height / 1920)))
    line_spacing = max(4, int(caption_style.line_spacing * (height / 1920)))
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    text = format_caption((subtitle or "").strip() or "(No subtitle)")
    lines = text.split("\n") if text else ["(No subtitle)"]

    line_heights: list[int] = []
    line_widths: list[int] = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(max(1, bbox[2] - bbox[0]))
        line_heights.append(max(1, bbox[3] - bbox[1]))

    block_height = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
    max_line_width = max(line_widths) if line_widths else width // 2

    margin = max(20, int(caption_style.bottom_margin * (height / 1920)))
    if caption_style.position == "top":
        y_start = margin
    elif caption_style.position == "center":
        y_start = max(0, (height - block_height) // 2)
    else:
        y_start = max(0, height - margin - block_height)

    box_padding_x = max(16, width // 40)
    box_padding_y = max(10, height // 90)
    box_left = max(0, (width - max_line_width) // 2 - box_padding_x)
    box_right = min(width, (width + max_line_width) // 2 + box_padding_x)
    box_top = max(0, y_start - box_padding_y)
    box_bottom = min(height, y_start + block_height + box_padding_y)
    draw.rounded_rectangle(
        [(box_left, box_top), (box_right, box_bottom)],
        radius=max(8, width // 90),
        fill=(0, 0, 0, 135),
    )

    y = y_start
    for i, line in enumerate(lines):
        line_w = line_widths[i]
        x = max(0, (width - line_w) // 2)
        for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2)]:
            draw.text((x + dx, y + dy), line, fill=(0, 0, 0, 220), font=font)
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)
        y += line_heights[i] + line_spacing

    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _default_scene_captions(media_files: list[Path], timeline_path: Path, *, aspect_ratio: str, font_size: int) -> list[str]:
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

    caption_max_lines, caption_max_chars = _caption_wrap_settings(aspect_ratio, font_size)

    captions: list[str] = []
    for i, media_path in enumerate(media_files, start=1):
        from_timeline = caption_by_path.get(str(media_path), "").strip()
        if from_timeline:
            captions.append(format_caption(from_timeline, max_lines=caption_max_lines, max_chars_per_line=caption_max_chars))
            continue
        scene_number = _scene_number_from_path(media_path) or i
        from_scene_excerpt = script_excerpt_by_index.get(scene_number, "").strip()
        if from_scene_excerpt:
            captions.append(format_caption(from_scene_excerpt, max_lines=caption_max_lines, max_chars_per_line=caption_max_chars))
            continue
        fallback = script_chunks[i - 1] if i - 1 < len(script_chunks) else ""
        formatted = format_caption(fallback, max_lines=caption_max_lines, max_chars_per_line=caption_max_chars)
        captions.append(formatted or f"Scene {i}")
    return captions


def _collect_scene_captions(
    media_files: list[Path],
    timeline_path: Path,
    *,
    caption_style: CaptionStyle,
    burn_captions: bool,
    aspect_ratio: str,
) -> list[str]:
    state_key = f"video_scene_captions::{timeline_path}"
    if state_key not in st.session_state or len(st.session_state[state_key]) != len(media_files):
        st.session_state[state_key] = _normalize_caption_list(_default_scene_captions(media_files, timeline_path, aspect_ratio=aspect_ratio, font_size=caption_style.font_size), len(media_files))

    caption_max_lines, caption_max_chars = _caption_wrap_settings(aspect_ratio, caption_style.font_size)
    captions: list[str] = _normalize_caption_list(st.session_state[state_key], len(media_files))
    st.session_state[state_key] = captions
    for idx, media_path in enumerate(media_files, start=1):
        with st.expander(f"Scene {idx}: {media_path.name}"):
            if media_path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}:
                st.video(str(media_path))
                st.caption(f"Subtitle preview: {captions[idx - 1] or '(No subtitle)'}")
            else:
                st.image(
                    _render_subtitle_preview(
                        media_path,
                        captions[idx - 1],
                        caption_style=caption_style,
                        burn_captions=burn_captions,
                    ),
                    width="stretch",
                )
            edited_caption = st.text_area(
                "Subtitle for this scene",
                value=captions[idx - 1],
                height=90,
                key=f"video_scene_caption_{idx}_{media_path.name}",
            )
            captions[idx - 1] = format_caption(edited_caption, max_lines=caption_max_lines, max_chars_per_line=caption_max_chars) or f"Scene {idx}"

    captions = _normalize_caption_list(captions, len(media_files))
    st.session_state[state_key] = captions
    return captions


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

    project_options = [p.name for p in project_dirs]
    active_id = active_project_id()
    default_index = project_options.index(active_id) if active_id in project_options else 0
    project_name = st.selectbox("Project folder", project_options, index=default_index)
    st.session_state.project_id = project_name
    project_path = projects_root / project_name
    st.caption(f"Active project ID for new assets: {project_name}")
    upsert_project(project_name, project_name.replace("_", " "))
    _sb_store.upsert_project(project_name, project_name.replace("_", " "))

    images_dir = project_path / "assets/images"
    videos_dir = project_path / "assets/videos"
    audio_dir = project_path / "assets/audio"
    music_dir = project_path / "assets/music"
    renders_dir = project_path / "renders"

    images = sorted([p for p in images_dir.glob("*.*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
    videos = sorted([p for p in videos_dir.glob("*.*") if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}])
    media_files = sorted(images + videos, key=_media_sort_key)
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
        "Tip: timeline.json is kept in sync with the current media and settings. Use Generate timeline.json to force a manual refresh."
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
                _sb_store.upload_audio(project_name, destination.name, destination)
                st.success("Saved generated voiceover to assets/audio/voiceover.mp3.")
                st.rerun()

    voiceover_upload = st.file_uploader(
        "Upload voiceover audio (.mp3 or .wav)",
        type=["mp3", "wav"],
        key="video_voiceover_upload",
    )
    if voiceover_upload is not None:
        voiceover_signature = (voiceover_upload.name, int(voiceover_upload.size or 0))
        if st.session_state.get("video_voiceover_upload_signature") != voiceover_signature:
            audio_dir.mkdir(parents=True, exist_ok=True)
            destination = audio_dir / voiceover_upload.name
            destination.write_bytes(voiceover_upload.getbuffer())
            record_asset(project_name, "voiceover", destination)
            _sb_store.upload_audio(project_name, destination.name, destination)
            st.session_state.video_voiceover_upload_signature = voiceover_signature
            st.success(f"Saved {voiceover_upload.name} to assets/audio.")
            st.rerun()
    else:
        st.session_state.pop("video_voiceover_upload_signature", None)

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
            music_signature = (uploaded_music.name, int(uploaded_music.size or 0))
            if st.session_state.get("video_music_upload_signature") != music_signature:
                music_dir.mkdir(parents=True, exist_ok=True)
                destination = music_dir / uploaded_music.name
                destination.write_bytes(uploaded_music.getbuffer())
                record_asset(project_name, "music", destination)
                st.session_state.video_music_upload_signature = music_signature
                st.success(f"Saved {uploaded_music.name} to assets/music.")
                st.rerun()
        else:
            st.session_state.pop("video_music_upload_signature", None)
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
            min_value=24,
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

    preview_burn_captions, preview_caption_style = _preview_caption_style(meta_defaults)

    st.markdown("### Scene subtitle review")
    if media_files:
        scene_captions = _collect_scene_captions(
            media_files,
            timeline_path,
            caption_style=preview_caption_style,
            burn_captions=preview_burn_captions,
            aspect_ratio=str(st.session_state.get("video_aspect_ratio", meta_defaults.get("aspect_ratio", "9:16"))),
        )
        if st.button("Auto-fill subtitles from scene script excerpts", width="stretch", key="video_auto_captions"):
            scene_captions = _normalize_caption_list(_default_scene_captions(media_files, timeline_path, aspect_ratio=aspect_ratio, font_size=caption_style.font_size), len(media_files))
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
                min_value=24,
                max_value=96,
                value=selected_caption_style.font_size,
                step=2,
                help="Font size in output pixels for burned-in captions.",
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
        include_voiceover_default = True
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
            value=bool(meta_defaults.get("enable_motion", True)),
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

    narration_wpm = st.slider(
        "Narration pace (WPM)",
        min_value=100,
        max_value=220,
        value=int(meta_defaults.get("narration_wpm", 160)),
        step=5,
        key="video_narration_wpm",
        help="Used for voiceover-aware per-scene timing based on script excerpt word counts.",
    )
    narration_min_sec = st.slider(
        "Min scene duration (seconds)",
        min_value=0.5,
        max_value=6.0,
        value=float(meta_defaults.get("narration_min_sec", 1.5)),
        step=0.1,
        key="video_narration_min_sec",
    )
    narration_max_sec = st.slider(
        "Max scene duration (seconds)",
        min_value=3.0,
        max_value=20.0,
        value=float(meta_defaults.get("narration_max_sec", 12.0)),
        step=0.5,
        key="video_narration_max_sec",
    )

    render_timeout_sec = st.number_input(
        "FFmpeg command timeout (seconds)",
        min_value=60,
        max_value=7200,
        value=int(meta_defaults.get("render_timeout_sec", 1200)),
        step=30,
        key="video_render_timeout_sec",
        help="Stops any single FFmpeg command that hangs too long; cached scene clips make retries faster.",
    )
    allow_silent_build = st.checkbox(
        "Allow silent build when voiceover is missing",
        value=False,
        key="video_allow_silent_build",
    )
    allow_long_timeout = st.checkbox(
        "Allow timeout above 45 minutes",
        value=False,
        key="video_allow_long_timeout",
    )

    timeline_meta_overrides = {
        "title": title,
        "aspect_ratio": aspect_ratio,
        "fps": int(fps),
        "scene_duration": effective_scene_duration,
        "burn_captions": burn_captions,
        "caption_style": selected_caption_style.model_dump(),
        "include_voiceover": include_voiceover,
        "include_music": include_music,
        "enable_motion": enable_motion,
        "crossfade": crossfade,
        "crossfade_duration": crossfade_duration,
        "transition_types": st.session_state.get("scene_transition_types", []),
        "music": {"volume_db": music_volume_db},
        "narration_wpm": narration_wpm,
        "narration_min_sec": narration_min_sec,
        "narration_max_sec": narration_max_sec,
        "render_timeout_sec": int(render_timeout_sec),
    }

    if media_files:
        try:
            sync_timeline_for_project(
                project_path=project_path,
                project_id=project_name,
                title=title,
                media_files=media_files,
                session_scenes=st.session_state.get("scenes", []),
                scene_captions=scene_captions,
                meta_overrides=timeline_meta_overrides,
            )
        except ValueError as exc:
            st.error(f"Timeline sync failed: {exc}")

    st.markdown("### Actions")
    if st.button("Generate timeline.json", width="stretch", key="video_generate_timeline"):
        if not media_files:
            st.error("No scene media found in assets/images or assets/videos. Add media to generate a timeline.")
        elif include_voiceover and not audio_files:
            st.error("Voiceover is enabled but no audio found in assets/audio/. Add a voiceover file first.")
        else:
            try:
                sync_timeline_for_project(
                    project_path=project_path,
                    project_id=project_name,
                    title=title,
                    media_files=media_files,
                    session_scenes=st.session_state.get("scenes", []),
                    scene_captions=scene_captions,
                    meta_overrides=timeline_meta_overrides,
                )
            except ValueError as exc:
                st.error(f"timeline.json generation failed: {exc}")
            else:
                st.success("timeline.json generated.")

    with st.expander("Debug: Captions & Timeline", expanded=False):
        st.write(f"ffmpeg on PATH: {shutil.which('ffmpeg')}")
        try:
            st.write(f"Resolved ffmpeg: {get_ffmpeg_exe()}")
        except Exception as exc:
            st.write(f"Resolved ffmpeg: unavailable ({exc})")
        st.write(f"len(images): {len(media_files)}")
        st.write(f"len(scene_captions): {len(scene_captions)}")
        _render_debug_samples(scene_captions, "Scene captions")

        if timeline_path.exists():
            try:
                timeline_debug = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                st.error(f"Unable to parse timeline.json for debugging: {exc}")
            else:
                st.write(f"len(timeline.scenes): {len(timeline_debug.scenes)}")
                _render_debug_samples([scene.caption or "" for scene in timeline_debug.scenes], "timeline.scenes[*].caption")
                _render_debug_samples([scene.image_path for scene in timeline_debug.scenes], "timeline.scenes[*].image_path")
                st.write("timeline.meta.caption_style:")
                st.json(timeline_debug.meta.caption_style.model_dump())
                st.write(f"timeline.meta.burn_captions: {timeline_debug.meta.burn_captions}")
        else:
            st.caption("timeline.json not found.")

        debug_srt_path = renders_dir / "captions.srt"
        st.write(f"captions.srt exists: {debug_srt_path.exists()}")
        if debug_srt_path.exists():
            srt_lines = debug_srt_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            st.write(f"captions.srt line count: {len(srt_lines)}")
            first_lines = "\n".join(srt_lines[:30])
            last_lines = "\n".join(srt_lines[-30:])
            st.markdown("**captions.srt first 30 lines**")
            st.code(first_lines or "(empty)")
            st.markdown("**captions.srt last 30 lines**")
            st.code(last_lines or "(empty)")

    if st.button("Render video (FFmpeg)", width="stretch", key="video_render"):
        if not media_files:
            st.error("No scene media found in assets/images or assets/videos. Add media before rendering.")
            return
        if include_voiceover and not audio_files:
            st.error("Voiceover is enabled but no audio found in assets/audio/. Add a voiceover file first.")
            return
        if not timeline_path.exists():
            st.error("timeline.json is missing. Click Generate timeline.json first.")
            return

        try:
            timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            st.error(f"Unable to read timeline.json: {exc}")
            return

        final_output_path = renders_dir / "final.mp4"
        if not _validate_render_preflight(
            timeline=timeline,
            media_files=media_files,
            output_path=final_output_path,
            allow_silent_build=allow_silent_build,
        ):
            return

        try:
            ensure_ffmpeg_exists()
        except FFmpegNotFoundError as exc:
            st.error(str(exc))
        else:
            renders_dir.mkdir(parents=True, exist_ok=True)
            log_path = renders_dir / "render_logs" / "ffmpeg_last.log"
            report_path = renders_dir / "render_report.json"
            timeout_seconds = _compute_render_timeout(
                duration_seconds=float(getattr(timeline, "total_duration", 0.0) or 0.0),
                user_timeout_seconds=int(render_timeout_sec),
                allow_long_timeout=allow_long_timeout,
            )
            render_timeline_path = timeline_path
            if allow_silent_build and timeline.meta.include_voiceover:
                voice_path = timeline.meta.voiceover.path if timeline.meta.voiceover else ""
                if not voice_path or not Path(voice_path).exists() or Path(voice_path).stat().st_size <= 0:
                    timeline.meta.include_voiceover = False
                    timeline.meta.voiceover = None
                    render_timeline_path = renders_dir / "timeline.silent.json"
                    render_timeline_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
                    st.info("Rendering without voiceover because silent build is enabled and voiceover is missing.")

            with st.spinner("Rendering video with FFmpeg..."):
                try:
                    render_video_from_timeline(
                        render_timeline_path,
                        final_output_path,
                        log_path=log_path,
                        report_path=report_path,
                        command_timeout_sec=float(timeout_seconds),
                        max_width=1280,
                    )
                except subprocess.CalledProcessError as exc:
                    st.error("FFmpeg failed.")
                    stdout_text = _render_error_output_text(exc.output)
                    stderr_text = _render_error_output_text(exc.stderr)
                    if stdout_text:
                        st.markdown("#### ffmpeg stdout")
                        st.code(stdout_text, language="bash")
                    if stderr_text:
                        st.markdown("#### ffmpeg stderr")
                        st.code(stderr_text, language="bash")
                    st.markdown("#### Python traceback")
                    st.code(traceback.format_exc(), language="python")
                    failure_log = _tail_file(log_path, lines=60)
                    if failure_log:
                        st.markdown("#### Last ~60 ffmpeg stderr/stdout lines")
                        st.code(failure_log, language="bash")
                    report = _load_render_report(report_path)
                    if report:
                        st.markdown("#### Structured render report")
                        st.json(report)
                    return
                except (RuntimeError, FileNotFoundError, ValueError):
                    st.error("Video render crashed.")
                    st.markdown("#### Python traceback")
                    st.code(traceback.format_exc(), language="python")
                    failure_log = _tail_file(log_path, lines=60)
                    if failure_log:
                        st.markdown("#### Last ~60 ffmpeg stderr/stdout lines")
                        st.code(failure_log, language="bash")
                    report = _load_render_report(report_path)
                    if report:
                        st.markdown("#### Structured render report")
                        st.json(report)
                    return
                else:
                    st.success("Render complete.")
                    _sb_store.upload_video(project_name, "final.mp4", final_output_path)

    st.markdown("### Render output")
    video_path = renders_dir / "final.mp4"
    srt_path = renders_dir / "captions.srt"
    log_path = renders_dir / "render_logs" / "ffmpeg_last.log"
    report_path = renders_dir / "render_report.json"

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

    report = _load_render_report(report_path)
    if report:
        st.markdown("#### Render report")
        st.json(report)
