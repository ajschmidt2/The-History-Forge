import streamlit as st

from utils import generate_lucky_topic, generate_research_brief, generate_script

from src.ui.state import clear_downstream, openai_error_message, script_ready


def tab_generate_script() -> None:
    st.subheader("Generate script")

    c1, c2 = st.columns([3, 1])
    with c1:
        st.session_state.topic = st.text_input(
            "Topic",
            value=st.session_state.topic,
            placeholder="e.g., The Rise of Rome",
        )
    with c2:
        if st.button("🎲 I'm Feeling Lucky", width="stretch"):
            try:
                st.session_state.topic = generate_lucky_topic()
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                st.error(openai_error_message(exc))
                return
            st.session_state.project_title = st.session_state.topic
            st.toast(st.session_state.topic)
            clear_downstream("script")

    st.session_state.length = st.selectbox(
        "Length",
        ["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"],
        index=["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"].index(st.session_state.length)
        if st.session_state.length in ["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"]
        else 1,
    )
    st.session_state.tone = st.selectbox(
        "Tone",
        ["Documentary", "Cinematic", "Mysterious", "Playful"],
        index=["Documentary", "Cinematic", "Mysterious", "Playful"].index(st.session_state.tone)
        if st.session_state.tone in ["Documentary", "Cinematic", "Mysterious", "Playful"]
        else 0,
    )

    st.session_state.audience = st.text_input(
        "Audience",
        value=st.session_state.audience,
        placeholder="e.g., General audience interested in hidden history",
    )
    st.session_state.story_angle = st.text_input(
        "Angle",
        value=st.session_state.story_angle,
        placeholder="e.g., Focus on causes and long-term consequences",
    )

    if st.button("Generate Research Brief", width="stretch"):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic before generating a brief.")
            return
        with st.spinner("Generating research brief..."):
            try:
                st.session_state.research_brief_text = generate_research_brief(
                    topic=st.session_state.topic,
                    tone=st.session_state.tone,
                    length=st.session_state.length,
                    audience=st.session_state.audience,
                    angle=st.session_state.story_angle,
                )
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                st.error(openai_error_message(exc))
                return
        st.toast("Research brief generated.")

    if st.session_state.research_brief_text.strip():
        with st.expander("Research brief", expanded=False):
            st.markdown(st.session_state.research_brief_text)
            st.checkbox(
                "Use this brief to generate script",
                key="use_research_brief_for_script",
            )

    if st.button("Generate Script", type="primary", width="stretch"):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic or use I'm Feeling Lucky.")
            return
        brief_for_script = st.session_state.research_brief_text if st.session_state.use_research_brief_for_script else ""
        with st.spinner("Generating script..."):
            try:
                generated_script = generate_script(
                    topic=st.session_state.topic,
                    length=st.session_state.length,
                    tone=st.session_state.tone,
                    audience=st.session_state.audience,
                    angle=st.session_state.story_angle,
                    research_brief=brief_for_script,
                )
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                st.error(openai_error_message(exc))
                return
        st.session_state.script_text = generated_script
        st.session_state.pending_script_text_input = generated_script
        st.session_state.project_title = st.session_state.topic or st.session_state.project_title
        clear_downstream("script")
        st.toast("Script generated.")
        st.rerun()

    if script_ready():
        with st.expander("Preview script", expanded=False):
            st.write(st.session_state.script_text)
