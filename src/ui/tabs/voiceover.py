from pathlib import Path

import streamlit as st

from src.audio import (
    TTS_PROVIDER_ELEVENLABS,
    TTS_PROVIDER_OPENAI,
    get_openai_tts_models,
    get_openai_tts_voices,
    get_tts_provider_options,
    resolve_tts_settings,
)
from src.workflow.project_io import load_project_payload, save_project_payload
from utils import Scene
from src.ui.state import DEFAULT_VOICE_ID, active_project_id, save_voice_id, script_ready
from src.ui.timeline_sync import sync_timeline_for_project
from src.video.timeline_builder import compute_scene_durations
from src.video.utils import get_media_duration
from src.workflow import PipelineOptions, StepStatus, run_generate_voiceover


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
        pass


def _persist_tts_settings() -> None:
    project_id = active_project_id()
    payload = load_project_payload(project_id)
    payload.update({
        "tts_provider": st.session_state.get("tts_provider", TTS_PROVIDER_ELEVENLABS),
        "voice_id": st.session_state.get("voice_id", DEFAULT_VOICE_ID),
        "elevenlabs_voice_id": st.session_state.get("voice_id", DEFAULT_VOICE_ID),
        "openai_tts_model": st.session_state.get("openai_tts_model", "gpt-4o-mini-tts"),
        "openai_tts_voice": st.session_state.get("openai_tts_voice", "alloy"),
        "openai_tts_instructions": st.session_state.get("openai_tts_instructions", ""),
    })
    save_project_payload(project_id, payload)


def tab_voiceover() -> None:
    st.subheader("Voiceover")

    if not script_ready():
        st.warning("Paste or generate a script first.")
        return

    project_settings = resolve_tts_settings(load_project_payload(active_project_id()))
    st.session_state.setdefault("tts_provider", project_settings.provider)
    st.session_state.setdefault("openai_tts_model", project_settings.openai_tts_model)
    st.session_state.setdefault("openai_tts_voice", project_settings.openai_tts_voice)
    st.session_state.setdefault("openai_tts_instructions", project_settings.openai_tts_instructions)

    provider_options = get_tts_provider_options()
    st.session_state.tts_provider = st.selectbox(
        "Voice Provider",
        options=provider_options,
        index=provider_options.index(st.session_state.get("tts_provider", TTS_PROVIDER_ELEVENLABS)),
        format_func=lambda p: "ElevenLabs" if p == TTS_PROVIDER_ELEVENLABS else "OpenAI",
    )

    voice_ids = [str(v or "").strip() for v in st.session_state.get("voice_ids", []) if str(v or "").strip()]
    if DEFAULT_VOICE_ID not in voice_ids:
        voice_ids.insert(0, DEFAULT_VOICE_ID)
    st.session_state.voice_ids = voice_ids

    current_voice_id = str(st.session_state.get("voice_id", "") or "").strip() or DEFAULT_VOICE_ID
    if current_voice_id not in st.session_state.voice_ids:
        st.session_state.voice_ids.append(current_voice_id)

    if st.session_state.tts_provider == TTS_PROVIDER_ELEVENLABS:
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
    else:
        model_options = get_openai_tts_models()
        voice_options = get_openai_tts_voices()
        current_model = str(st.session_state.get("openai_tts_model", "gpt-4o-mini-tts") or "gpt-4o-mini-tts")
        current_voice = str(st.session_state.get("openai_tts_voice", "alloy") or "alloy").lower()
        if current_model not in model_options:
            current_model = "gpt-4o-mini-tts"
        if current_voice not in voice_options:
            current_voice = "alloy"

        st.session_state.openai_tts_model = st.selectbox("OpenAI TTS Model", options=model_options, index=model_options.index(current_model))
        st.session_state.openai_tts_voice = st.selectbox("OpenAI Voice", options=voice_options, index=voice_options.index(current_voice))
        st.session_state.openai_tts_instructions = st.text_area(
            "Speaking style instructions (optional)",
            value=st.session_state.get("openai_tts_instructions", ""),
            placeholder="Example: Calm, reflective pacing with warm documentary tone.",
            help="This controls tone/style guidance, especially with gpt-4o-mini-tts.",
        )
        st.caption("Tip: gpt-4o-mini-tts supports instruction-driven delivery style.")

    controls_left, controls_right = st.columns([1, 1])
    with controls_left:
        if st.button("Save voice settings", width="stretch"):
            try:
                save_voice_id(st.session_state.get("voice_id", DEFAULT_VOICE_ID))
            except OSError as exc:
                st.error(f"Could not save voice ID: {exc}")
            _persist_tts_settings()
            st.toast("Voice settings saved.")
    with controls_right:
        if st.button("Generate voiceover", type="primary", width="stretch"):
            if st.session_state.tts_provider == TTS_PROVIDER_OPENAI and not st.session_state.get("openai_tts_voice"):
                st.error("OpenAI voice is required.")
                return
            try:
                save_voice_id(st.session_state.get("voice_id", DEFAULT_VOICE_ID))
            except OSError:
                pass
            _persist_tts_settings()
            with st.spinner("Generating voiceover..."):
                result = run_generate_voiceover(
                    active_project_id(),
                    PipelineOptions(
                        tts_provider=st.session_state.tts_provider,
                        voice_id=st.session_state.get("voice_id", DEFAULT_VOICE_ID),
                        elevenlabs_voice_id=st.session_state.get("voice_id", DEFAULT_VOICE_ID),
                        openai_tts_model=st.session_state.get("openai_tts_model", "gpt-4o-mini-tts"),
                        openai_tts_voice=st.session_state.get("openai_tts_voice", "alloy"),
                        openai_tts_instructions=st.session_state.get("openai_tts_instructions", ""),
                    ),
                )
            if result.status != StepStatus.COMPLETED:
                st.session_state.voiceover_error = result.message
                st.error(result.message)
            else:
                output_path = Path(str(result.outputs.get("voiceover_path", "")))
                if output_path.exists():
                    st.session_state.voiceover_bytes = output_path.read_bytes()
                    st.session_state.voiceover_saved_path = str(output_path)
                    st.session_state.voiceover_error = None
                    _auto_adjust_scene_lengths_to_voiceover(output_path)
                    st.toast("Voiceover generated and scene timings auto-adjusted.")
            st.rerun()

    if st.session_state.voiceover_error:
        st.error(st.session_state.voiceover_error)

    if st.session_state.voiceover_bytes:
        st.audio(st.session_state.voiceover_bytes, format="audio/mp3")
        if st.session_state.voiceover_saved_path:
            st.caption(f"Saved to {st.session_state.voiceover_saved_path}")
