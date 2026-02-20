from pathlib import Path

import streamlit as st

from utils import generate_voiceover
from src.storage import record_asset
from src.ui.state import active_project_id, save_voice_id, script_ready


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
                st.toast("Voiceover generated.")
            st.rerun()

    if st.session_state.voiceover_error:
        st.error(st.session_state.voiceover_error)

    if st.session_state.voiceover_bytes:
        st.audio(st.session_state.voiceover_bytes, format="audio/mp3")
        if st.session_state.voiceover_saved_path:
            st.caption(f"Saved to {st.session_state.voiceover_saved_path}")
