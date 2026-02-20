import json
import zipfile
from io import BytesIO

import streamlit as st

from src.ui.state import scenes_ready, script_ready


def build_zip() -> bytes:
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
