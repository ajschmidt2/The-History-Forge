import streamlit as st

from src.supabase_storage import upload_script
from src.ui.state import active_project_id, clear_downstream, save_project_state


def tab_paste_script() -> None:
    st.subheader("Paste your own script")

    if st.session_state.pending_script_text_input:
        st.session_state.script_text_input = st.session_state.pending_script_text_input
        st.session_state.pending_script_text_input = ""

    st.session_state.project_title = st.text_input(
        "Project Title",
        value=st.session_state.project_title,
        placeholder="e.g., The Rise of Rome",
    )

    st.text_area(
        "Script",
        key="script_text_input",
        height=320,
        placeholder="Paste your narration script here...",
    )

    if st.button("Use this script →", type="primary", width="stretch"):
        st.session_state.script_text = st.session_state.script_text_input
        clear_downstream("script")
        save_project_state(active_project_id())
        try:
            upload_script(active_project_id(), st.session_state.script_text_input)
        except Exception:
            pass
        st.toast("Script loaded.")
        st.rerun()
