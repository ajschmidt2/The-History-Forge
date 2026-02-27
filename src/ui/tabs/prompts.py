import streamlit as st

from src.ui.state import clear_downstream, scenes_ready

_MAX_CHARACTERS = 5
_MAX_OBJECTS = 14


def _render_consistency_panel() -> None:
    """Character & Object Consistency registry editor."""
    with st.expander("Character & Object Consistency", expanded=False):
        st.caption(
            "Define up to 5 characters and 14 objects. "
            "Their visual descriptions are injected into every image prompt so they look "
            "the same across all scenes (storyboards, ad campaigns, etc.)."
        )

        # ── Characters ──────────────────────────────────────────────────────────
        st.markdown("**Characters** (up to 5)")
        characters: list[dict] = list(st.session_state.get("character_registry", []))

        remove_char: int | None = None
        for i, char in enumerate(characters):
            col_name, col_desc, col_rm = st.columns([2, 4, 0.45])
            with col_name:
                characters[i]["name"] = st.text_input(
                    "Character name",
                    value=char.get("name", ""),
                    key=f"char_name_{i}",
                    placeholder="e.g. Julius Caesar",
                    label_visibility="collapsed",
                )
            with col_desc:
                characters[i]["description"] = st.text_input(
                    "Character description",
                    value=char.get("description", ""),
                    key=f"char_desc_{i}",
                    placeholder="e.g. Roman senator, 50s, laurel wreath, white toga with purple border",
                    label_visibility="collapsed",
                )
            with col_rm:
                if st.button("✕", key=f"char_rm_{i}", help="Remove character"):
                    remove_char = i

        if remove_char is not None:
            characters.pop(remove_char)
            st.session_state.character_registry = characters
            st.rerun()

        if len(characters) < _MAX_CHARACTERS:
            if st.button("+ Add Character", key="char_add"):
                characters.append({"name": "", "description": ""})
                st.session_state.character_registry = characters
                st.rerun()

        st.session_state.character_registry = characters

        st.divider()

        # ── Objects ──────────────────────────────────────────────────────────────
        st.markdown("**Objects** (up to 14)")
        objects: list[dict] = list(st.session_state.get("object_registry", []))

        remove_obj: int | None = None
        for i, obj in enumerate(objects):
            col_name, col_desc, col_rm = st.columns([2, 4, 0.45])
            with col_name:
                objects[i]["name"] = st.text_input(
                    "Object name",
                    value=obj.get("name", ""),
                    key=f"obj_name_{i}",
                    placeholder="e.g. The Gladius",
                    label_visibility="collapsed",
                )
            with col_desc:
                objects[i]["description"] = st.text_input(
                    "Object description",
                    value=obj.get("description", ""),
                    key=f"obj_desc_{i}",
                    placeholder="e.g. Short Roman sword, iron blade, leather-wrapped wooden grip",
                    label_visibility="collapsed",
                )
            with col_rm:
                if st.button("✕", key=f"obj_rm_{i}", help="Remove object"):
                    remove_obj = i

        if remove_obj is not None:
            objects.pop(remove_obj)
            st.session_state.object_registry = objects
            st.rerun()

        if len(objects) < _MAX_OBJECTS:
            if st.button("+ Add Object", key="obj_add"):
                objects.append({"name": "", "description": ""})
                st.session_state.object_registry = objects
                st.rerun()

        st.session_state.object_registry = objects

        # Summary
        defined = [c for c in characters if c.get("name", "").strip()]
        defined_obj = [o for o in objects if o.get("name", "").strip()]
        if defined or defined_obj:
            st.caption(
                f"{len(defined)} character(s) · {len(defined_obj)} object(s) defined — "
                "these will be injected into all generated prompts."
            )


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

    _render_consistency_panel()

    generate_col, reset_col = st.columns([3, 2])
    with generate_col:
        generate_prompts_clicked = st.button("Generate prompts for all scenes", type="primary", width="stretch")
    with reset_col:
        reset_prompts_clicked = st.button(
            "Reset prompts",
            width="stretch",
            help="Clear all generated image prompts so you can regenerate from scratch.",
        )

    if generate_prompts_clicked:
        from utils import generate_prompts_for_scenes

        with st.spinner("Generating prompts..."):
            st.session_state.scenes = generate_prompts_for_scenes(
                st.session_state.scenes,
                tone=st.session_state.tone,
                style=st.session_state.visual_style,
                characters=st.session_state.get("character_registry", []),
                objects=st.session_state.get("object_registry", []),
            )
            for s in st.session_state.scenes:
                st.session_state[f"prompt_{s.index}"] = s.image_prompt
        clear_downstream("prompts")
        st.toast("Prompts generated.")
        st.rerun()

    if reset_prompts_clicked:
        for scene in st.session_state.scenes:
            scene.image_prompt = ""
            st.session_state[f"prompt_{scene.index}"] = ""
        clear_downstream("prompts")
        st.toast("Prompts reset.")
        st.rerun()

    st.divider()
    for s in st.session_state.scenes:
        s.image_prompt = st.text_area(
            f"{s.index:02d} — {s.title} prompt",
            value=s.image_prompt or "",
            height=110,
            key=f"prompt_{s.index}",
        )
