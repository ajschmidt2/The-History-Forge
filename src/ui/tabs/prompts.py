import streamlit as st

from utils import generate_prompts_for_scenes

from src.ui.state import clear_downstream, scenes_ready


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

    if st.button("Generate prompts for all scenes", type="primary", width="stretch"):
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
