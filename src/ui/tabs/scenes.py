import streamlit as st

from utils import split_script_into_scenes

from src.ui.state import clear_downstream, scenes_ready, script_ready


def tab_create_scenes() -> None:
    st.subheader("Create scenes")

    if not script_ready():
        st.warning("Paste or generate a script first.")
        return

    st.session_state.max_scenes = st.number_input(
        "Number of scenes",
        min_value=3,
        max_value=75,
        value=int(st.session_state.max_scenes),
        step=1,
    )

    if st.button("Split script into scenes", type="primary", width="stretch"):
        with st.spinner("Splitting script..."):
            st.session_state.scenes = split_script_into_scenes(
                st.session_state.script_text,
                max_scenes=int(st.session_state.max_scenes),
            )
        clear_downstream("scenes")
        st.toast(f"Created {len(st.session_state.scenes)} scenes.")
        st.rerun()

    if not scenes_ready():
        st.info("No scenes yet.")
        return

    st.divider()
    st.markdown("### Scene list (editable)")
    pending_edits: dict[int, dict[str, str]] = {}
    for s in st.session_state.scenes:
        with st.expander(f"{s.index:02d} — {s.title}", expanded=False):
            st.text_input("Title", value=s.title, key=f"title_{s.index}")
            st.text_area("Excerpt", value=s.script_excerpt, height=140, key=f"txt_{s.index}")
            st.text_area("Visual intent", value=s.visual_intent, height=90, key=f"vi_{s.index}")
            pending_edits[s.index] = {
                "title": st.session_state.get(f"title_{s.index}", s.title),
                "script_excerpt": st.session_state.get(f"txt_{s.index}", s.script_excerpt),
                "visual_intent": st.session_state.get(f"vi_{s.index}", s.visual_intent),
            }

    for s in st.session_state.scenes:
        edits = pending_edits.get(s.index, {})
        s.title = edits.get("title", s.title)
        s.script_excerpt = edits.get("script_excerpt", s.script_excerpt)
        s.visual_intent = edits.get("visual_intent", s.visual_intent)

    st.caption("Tip: prompts + images are generated in the next tabs.")
