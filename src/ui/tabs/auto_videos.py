""" src/ui/tabs/auto_videos.py
──────────────────────────
Tab for browsing and assembling daily auto-generated videos.
Pulls projects with status='ready' from Supabase, lets user preview assets,
then assembles final MP4 using MoviePy.
"""

import streamlit as st
import requests
import tempfile
import os
from pathlib import Path
from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeAudioClip,
    concatenate_videoclips,
)

import src.supabase_storage as _sb_store

CROSSFADE_DURATION = 1.0   # seconds
IMAGE_DURATION     = 4.0   # seconds each image stays on screen
MUSIC_VOLUME       = 0.15  # background music volume (0.0 to 1.0)
OUTPUT_FPS         = 24


def _download_to_temp(url: str, suffix: str) -> str:
    """Download a URL to a temp file, return the path."""
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(r.content)
    tmp.close()
    return tmp.name


def _assemble_video(project: dict, progress_bar) -> str:
    """
    Combine images + voiceover + background music into an MP4.
    Returns path to the output file.
    """
    image_urls    = project.get("image_urls") or []
    voiceover_url = project.get("voiceover_path")
    music_url     = project.get("music_path")

    if not image_urls:
        raise ValueError("No images found for this project.")
    if not voiceover_url:
        raise ValueError("No voiceover found for this project.")

    progress_bar.progress(0.05, "Downloading voiceover...")
    voiceover_path = _download_to_temp(voiceover_url, ".mp3")

    progress_bar.progress(0.10, "Downloading background music...")
    music_path = _download_to_temp(music_url, ".mp3") if music_url else None

    progress_bar.progress(0.15, "Downloading images...")
    image_paths = []
    for i, url in enumerate(image_urls):
        if not url:
            continue
        path = _download_to_temp(url, ".png")
        image_paths.append(path)
        progress_bar.progress(
            0.15 + (i / len(image_urls)) * 0.35,
            f"Downloading image {i+1} of {len(image_urls)}...",
        )

    progress_bar.progress(0.50, "Building video clips...")

    # Load voiceover to get total duration
    voice_audio    = AudioFileClip(voiceover_path)
    total_duration = voice_audio.duration

    # Calculate image duration to fill the full voiceover length
    img_duration = max(
        IMAGE_DURATION,
        (total_duration + CROSSFADE_DURATION * len(image_paths)) / len(image_paths),
    )

    # Build image clips with crossfade
    clips = []
    for i, img_path in enumerate(image_paths):
        clip = ImageClip(img_path, duration=img_duration)
        clip = clip.resize(height=1080).crop(
            x_center=clip.w / 2,
            y_center=clip.h / 2,
            width=1920,
            height=1080,
        )
        if i > 0:
            clip = clip.crossfadein(CROSSFADE_DURATION)
        clips.append(clip)

    progress_bar.progress(0.65, "Combining clips...")
    video = concatenate_videoclips(clips, method="compose", padding=-CROSSFADE_DURATION)
    video = video.subclip(0, total_duration)

    progress_bar.progress(0.75, "Adding audio...")

    # Mix voiceover + background music
    if music_path:
        music_audio = AudioFileClip(music_path).volumex(MUSIC_VOLUME)
        # Loop music if shorter than video
        if music_audio.duration < total_duration:
            loops = int(total_duration / music_audio.duration) + 1
            from moviepy.editor import concatenate_audioclips
            music_audio = concatenate_audioclips([music_audio] * loops)
        music_audio  = music_audio.subclip(0, total_duration).audio_fadeout(2)
        final_audio  = CompositeAudioClip([voice_audio, music_audio])
    else:
        final_audio = voice_audio

    video = video.set_audio(final_audio)

    progress_bar.progress(0.85, "Rendering final video...")
    output_path = tempfile.mktemp(suffix=".mp4")
    video.write_videofile(
        output_path,
        fps=OUTPUT_FPS,
        codec="libx264",
        audio_codec="aac",
        threads=2,
        logger=None,
    )

    # Cleanup temp files
    for p in image_paths + [voiceover_path]:
        try:
            os.unlink(p)
        except Exception:
            pass
    if music_path:
        try:
            os.unlink(music_path)
        except Exception:
            pass

    progress_bar.progress(1.0, "Done!")
    return output_path


def tab_auto_videos() -> None:
    st.header("🤖 Auto Videos")
    st.caption("Daily auto-generated history videos ready to assemble.")

    sb = _sb_store.get_client()

    # Fetch completed projects from Supabase
    try:
        if sb is None:
            raise RuntimeError("Supabase is not configured.")
        res = (
            sb.table("projects")
            .select("id, title, script, image_urls, voiceover_path, music_path, status, created_at")
            .eq("status", "ready")
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        projects = res.data or []
    except Exception as e:
        st.error(f"Could not load projects: {e}")
        return

    if not projects:
        st.info("No completed auto-videos yet. The pipeline runs daily at 6am UTC.")
        return

    # Project selector
    project_titles = [f"{p['created_at'][:10]} — {p['title']}" for p in projects]
    selected_index = st.selectbox(
        "Select a project",
        range(len(projects)),
        format_func=lambda i: project_titles[i],
    )
    project = projects[selected_index]

    st.divider()

    # Preview section
    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Topic")
        st.write(project["title"])
        st.subheader("Script")
        st.text_area("", value=project.get("script", ""), height=200, disabled=True)

    with col2:
        st.subheader("Images")
        image_urls = project.get("image_urls") or []
        valid_urls = [u for u in image_urls if u]
        st.caption(f"{len(valid_urls)} images ready")
        if valid_urls:
            st.image(valid_urls[0], caption="First image preview", use_column_width=True)

        if project.get("voiceover_path"):
            st.subheader("Voiceover Preview")
            st.audio(project["voiceover_path"])

    st.divider()

    # Assemble button
    if st.button("🎬 Assemble Video", type="primary", use_container_width=True):
        if not valid_urls:
            st.error("No images available for this project.")
            return

        progress = st.progress(0.0, "Starting...")
        try:
            output_path = _assemble_video(project, progress)
            st.success("Video assembled successfully!")

            with open(output_path, "rb") as f:
                st.download_button(
                    label="⬇️ Download Video",
                    data=f,
                    file_name=f"{project['id']}.mp4",
                    mime="video/mp4",
                    use_container_width=True,
                )
            os.unlink(output_path)

        except Exception as e:
            st.error(f"Assembly failed: {e}")
            st.exception(e)
