from pathlib import Path

import streamlit as st

from utils import Scene, generate_voiceover
from src.storage import record_asset
import src.supabase_storage as _sb_store
from src.ui.state import DEFAULT_VOICE_ID, active_project_id, save_voice_id, script_ready
from src.ui.timeline_sync import sync_timeline_for_project
from src.video.timeline_builder import compute_scene_durations
from src.video.utils import get_media_duration


def _fit_scene_durations_to_voiceover(
    scenes: list[Scene],
    voiceover_duration: float,
    *,
    wpm: float,
    min_sec: float = 1.5,
    max_sec: float = 12.0,
) -> list[float]:
    excerpts = [str(getattr(scene, "script_excerpt", "") or "") for scene in scenes]
    durations = compute_scene_durations(excerpts, wpm=wpm, min_sec=min_sec, max_sec=max_sec)
    if voiceover_duration > 0 and sum(durations) > 0:
        scale = voiceover_duration / sum(durations)
        durations = [max(float(min_sec), d * scale) for d in durations]
        if sum(durations) > 0:
            correction = voiceover_duration / sum(durations)
            durations = [d * correction for d in durations]
    return durations


def _auto_adjust_scene_lengths_to_voiceover(voiceover_path: Path) -> None:
    scenes = st.session_state.get("scenes", [])
    if not scenes:
        return

    try:
        voiceover_duration = float(get_media_duration(voiceover_path))
    except Exception:
        return

    wpm = float(st.session_state.get("scene_wpm", 160) or 160)
    durations = _fit_scene_durations_to_voiceover(
        scenes,
        voiceover_duration,
        wpm=wpm,
        min_sec=1.5,
        max_sec=12.0,
    )
    if not durations:
        return

    for scene, duration in zip(scenes, durations):
        scene.estimated_duration_sec = float(duration)

    st.session_state.estimated_total_runtime_sec = round(sum(durations), 1)

    project_path = Path("data/projects") / active_project_id()
    try:
        sync_timeline_for_project(
            project_path=project_path,
            project_id=active_project_id(),
            title=st.session_state.get("project_title", active_project_id()),
            session_scenes=scenes,
        )
    except Exception:
        # Non-fatal: durations are still updated in session state.
        pass


def tab_voiceover() -> None:
    st.subheader("Voiceover (ElevenLabs)")

    if not script_ready():
        st.warning("Paste or generate a script first.")
        return

    voice_ids = [str(v or "").strip() for v in st.session_state.get("voice_ids", []) if str(v or "").strip()]
    if DEFAULT_VOICE_ID not in voice_ids:
        voice_ids.insert(0, DEFAULT_VOICE_ID)
    st.session_state.voice_ids = voice_ids

    current_voice_id = str(st.session_state.get("voice_id", "") or "").strip() or DEFAULT_VOICE_ID
    if current_voice_id not in st.session_state.voice_ids:
        st.session_state.voice_ids.append(current_voice_id)

    selected_voice_id = st.selectbox(
        "ElevenLabs Voice ID",
        options=st.session_state.voice_ids,
        index=st.session_state.voice_ids.index(current_voice_id),
        help="Default ID is hard-coded, and you can add more IDs below.",
    )
    st.session_state.voice_id = selected_voice_id

    new_voice_id = st.text_input(
        "Add new Voice ID",
        value="",
        placeholder="Paste another ElevenLabs voice_id and click Add",
    )
    if st.button("Add voice ID", width="stretch"):
        candidate = str(new_voice_id or "").strip()
        if not candidate:
            st.warning("Enter a voice ID first.")
        elif candidate in st.session_state.voice_ids:
            st.info("That voice ID already exists.")
            st.session_state.voice_id = candidate
        else:
            st.session_state.voice_ids.append(candidate)
            st.session_state.voice_id = candidate
            try:
                save_voice_id(candidate)
            except OSError as exc:
                st.error(f"Could not save voice ID: {exc}")
            else:
                st.toast("Voice ID added.")
            st.rerun()

    controls_left, controls_right = st.columns([1, 1])
    with controls_left:
        if st.button("Save voice ID", width="stretch"):
            try:
                save_voice_id(st.session_state.voice_id)
            except OSError as exc:
                st.error(f"Could not save voice ID: {exc}")
            else:
                st.toast("Voice ID saved.")
    with controls_right:
        if st.button("Generate voiceover", type="primary", width="stretch"):
            try:
                save_voice_id(st.session_state.voice_id)
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
                project_folder = Path("data/projects") / active_project_id() / "assets/audio"
                project_folder.mkdir(parents=True, exist_ok=True)
                output_path = project_folder / "voiceover.mp3"
                output_path.write_bytes(audio)
                st.session_state.voiceover_saved_path = str(output_path)
                record_asset(active_project_id(), "voiceover", output_path)
                _sb_store.upload_audio(active_project_id(), output_path.name, output_path)
                _auto_adjust_scene_lengths_to_voiceover(output_path)
                st.toast("Voiceover generated and scene timings auto-adjusted.")
            st.rerun()

    if st.session_state.voiceover_error:
        st.error(st.session_state.voiceover_error)

    if st.session_state.voiceover_bytes:
        st.audio(st.session_state.voiceover_bytes, format="audio/mp3")
        if st.session_state.voiceover_saved_path:
            st.caption(f"Saved to {st.session_state.voiceover_saved_path}")
