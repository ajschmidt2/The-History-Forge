import io
import json
import random
import zipfile
from typing import Any, Dict, List

import streamlit as st

from utils import (
    Scene,
    generate_script,
    split_script_into_scenes,
    generate_prompts_for_scenes,
    generate_image_for_scene,
    generate_voiceover,
    get_secret,
)


def require_login() -> None:
    pw = (
        st.secrets.get("APP_PASSCODE", "")
        or st.secrets.get("app_password", "")
        or get_secret("APP_PASSCODE", "")
        or get_secret("app_password", "")
    ).strip()
    if not pw:
        return
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if st.session_state.authenticated:
        return
    st.title("🔒 The History Forge")
    entered = st.text_input("Password", type="password")
    if st.button("Log in", use_container_width=True):
        if entered == pw:
            st.session_state.authenticated = True
            st.rerun()
        st.error("Incorrect password")
    st.stop()


def _sync_scene_order(scenes: List[Scene]) -> None:
    for idx, scene in enumerate(scenes, start=1):
        scene.index = idx


def _get_primary_image(scene: Scene) -> bytes | None:
    if scene.image_variations:
        idx = max(0, min(scene.primary_image_index, len(scene.image_variations) - 1))
        return scene.image_variations[idx]
    return scene.image_bytes


def build_export_zip(
    script: str,
    scenes: List[Scene],
    include_all_variations: bool,
    voiceover_bytes: bytes | None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("script.txt", script or "")
        export_scenes = [s for s in scenes if s.status != "deleted"]
        z.writestr("scenes.json", json.dumps([s.to_dict() for s in export_scenes], indent=2))
        if voiceover_bytes:
            z.writestr("voiceover.mp3", voiceover_bytes)
        for s in export_scenes:
            if include_all_variations and s.image_variations:
                for i, img in enumerate(s.image_variations, start=1):
                    if img:
                        z.writestr(f"images/scene_{s.index:02d}/variation_{i:02d}.png", img)
            else:
                primary = _get_primary_image(s)
                if primary:
                    z.writestr(f"images/scene_{s.index:02d}.png", primary)
    return buf.getvalue()


def init_state() -> None:
    st.session_state.setdefault("topic", "")
    st.session_state.setdefault("script_text", "")
    st.session_state.setdefault("scenes", [])
    st.session_state.setdefault("scene_prompts", {})
    st.session_state.setdefault("scene_images", {})
    st.session_state.setdefault("active_story_title", "Untitled Project")
    st.session_state.setdefault("aspect_ratio", "16:9")
    st.session_state.setdefault("visual_style", "Photorealistic cinematic")
    st.session_state.setdefault(
        "lucky_topics",
        [
            "The Rise of Rome",
            "The Fall of Constantinople",
            "The Spy Who Fooled Hitler",
            "The Lost City of Cahokia",
            "The Great Fire of London",
            "The Strange Death of Rasputin",
            "The Battle Won by an Eclipse",
            "The Silk Road's Hidden Empires",
            "The Mystery of the Mary Celeste",
            "The Day the Titanic Was Found",
        ],
    )



def get_scene_key(scene: Any, idx: int) -> str:
    if isinstance(scene, dict):
        return str(scene.get("id") or scene.get("scene_id") or f"idx_{idx}")
    if hasattr(scene, "id") and getattr(scene, "id"):
        return str(getattr(scene, "id"))
    return f"idx_{idx}"


def scene_title(scene: Any, idx: int) -> str:
    if isinstance(scene, dict):
        return scene.get("title") or f"Scene {idx + 1}"
    if hasattr(scene, "title") and getattr(scene, "title"):
        return getattr(scene, "title")
    return f"Scene {idx + 1}"


def scene_text(scene: Any) -> str:
    if isinstance(scene, dict):
        return scene.get("text") or scene.get("script") or scene.get("content") or ""
    if hasattr(scene, "script_excerpt"):
        return getattr(scene, "script_excerpt") or ""
    if hasattr(scene, "text"):
        return getattr(scene, "text") or ""
    if hasattr(scene, "script"):
        return getattr(scene, "script") or ""
    return ""


def _update_scene_prompt(scene: Scene, new_prompt: str) -> None:
    scene.image_prompt = new_prompt


def tab_paste_script() -> None:
    st.subheader("Paste your own script")
    st.caption("Paste an existing script and use it as the source for scenes, prompts, images, and export.")

    st.session_state.script_text = st.text_area(
        "Script",
        value=st.session_state.script_text,
        height=320,
        placeholder="Paste your script here...",
    )

    cols = st.columns([1, 3])
    with cols[0]:
        if st.button("Use Script →", type="primary", use_container_width=True):
            if not st.session_state.script_text.strip():
                st.warning("Paste a script first.")
            else:
                st.toast("Script loaded.")
                st.session_state.script = st.session_state.script_text
    with cols[1]:
        st.session_state.active_story_title = st.text_input(
            "Project Title",
            value=st.session_state.active_story_title,
            placeholder="e.g., The Rise of Rome",
        )


def tab_generate_script() -> None:
    st.subheader("Generate script")
    st.caption("Generate a script from a topic, or pick a random topic with 'I'm Feeling Lucky'.")

    c1, c2 = st.columns([3, 1])
    with c1:
        st.session_state.topic = st.text_input(
            "Topic",
            value=st.session_state.topic,
            placeholder="e.g., The Rise of Rome",
        )
    with c2:
        if st.button("🎲 I'm Feeling Lucky", use_container_width=True):
            st.session_state.topic = random.choice(st.session_state.lucky_topics)
            st.session_state.active_story_title = st.session_state.topic
            st.toast(f"Picked: {st.session_state.topic}")

    length_display = st.selectbox("Length", ["~1 min", "~3 min", "~5 min", "~10 min"], index=2)
    length_map = {
        "~1 min": "Short (~60 seconds)",
        "~3 min": "8–10 minutes",
        "~5 min": "8–10 minutes",
        "~10 min": "20–30 minutes",
    }
    tone = st.selectbox("Tone", ["Documentary", "Cinematic", "Mysterious", "Playful"], index=0)

    if st.button("Generate Script", type="primary", use_container_width=True):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic or use I'm Feeling Lucky.")
            return

        with st.spinner("Generating script..."):
            st.session_state.script_text = generate_script(
                topic=st.session_state.topic,
                length=length_map[length_display],
                tone=tone,
            )
        st.session_state.script = st.session_state.script_text
        st.session_state.active_story_title = st.session_state.topic
        st.toast("Script generated.")


def tab_create_scenes() -> None:
    st.subheader("Create scenes")
    st.caption("Split your script into scenes you can refine and storyboard.")

    if not st.session_state.script_text.strip():
        st.warning("Paste or generate a script first.")
        return

    c1, c2 = st.columns([1, 1])
    with c1:
        target_scenes = st.number_input("Target scenes", min_value=3, max_value=60, value=12, step=1)
    with c2:
        st.session_state.visual_style = st.selectbox(
            "Visual style",
            [
                "Photorealistic cinematic",
                "Illustrated cinematic",
                "Painterly",
                "Comic / graphic novel",
                "Vintage archival photo",
                "3D render",
                "Watercolor illustration",
                "Charcoal / pencil sketch",
            ],
            index=0,
        )

    st.session_state.aspect_ratio = st.selectbox(
        "Image aspect ratio",
        ["16:9", "9:16", "1:1"],
        index=0,
    )

    if st.button("Split into scenes", type="primary", use_container_width=True):
        with st.spinner("Splitting script into scenes..."):
            st.session_state.scenes = split_script_into_scenes(
                st.session_state.script_text,
                max_scenes=int(target_scenes),
            )
            _sync_scene_order(st.session_state.scenes)
            st.session_state.scene_prompts = {}
            st.session_state.scene_images = {}
        st.toast(f"Created {len(st.session_state.scenes)} scenes.")

    if not st.session_state.scenes:
        st.info("No scenes yet. Click 'Split into scenes'.")
        return

    st.divider()
    st.markdown("### Scene Editor")

    for i, sc in enumerate(st.session_state.scenes):
        key = get_scene_key(sc, i)
        with st.expander(f"{i + 1:02d} — {scene_title(sc, i)}", expanded=False):
            title_val = sc.title or f"Scene {i + 1}"
            text_val = scene_text(sc)
            new_title = st.text_input("Title", value=title_val, key=f"title_{key}")
            new_text = st.text_area("Scene text", value=text_val, key=f"text_{key}", height=140)
            sc.title = new_title
            sc.script_excerpt = new_text

            del_col, _ = st.columns([1, 5])
            with del_col:
                if st.button("Delete scene", key=f"del_{key}"):
                    st.session_state.scenes.pop(i)
                    _sync_scene_order(st.session_state.scenes)
                    st.rerun()


def tab_create_prompts() -> None:
    st.subheader("Create prompts")
    st.caption("Generate (and edit) image prompts for each scene.")

    if not st.session_state.scenes:
        st.warning("Create scenes first.")
        return

    if st.button("Generate prompts for all scenes", type="primary", use_container_width=True):
        with st.spinner("Generating prompts..."):
            st.session_state.scenes = generate_prompts_for_scenes(
                st.session_state.scenes,
                tone="Cinematic",
                style=st.session_state.visual_style,
            )
            for i, sc in enumerate(st.session_state.scenes):
                sid = get_scene_key(sc, i)
                st.session_state.scene_prompts[sid] = sc.image_prompt
        st.toast("Prompts ready.")

    st.divider()
    for i, sc in enumerate(st.session_state.scenes):
        sid = get_scene_key(sc, i)
        st.session_state.scene_prompts.setdefault(sid, sc.image_prompt or "")
        updated = st.text_area(
            f"{i + 1:02d} — {scene_title(sc, i)} prompt",
            value=st.session_state.scene_prompts[sid],
            key=f"prompt_{sid}",
            height=90,
        )
        st.session_state.scene_prompts[sid] = updated
        _update_scene_prompt(sc, updated)


def _store_scene_image(scene: Scene, image_bytes: bytes) -> None:
    if scene.image_variations:
        scene.image_variations.append(image_bytes)
        scene.primary_image_index = len(scene.image_variations) - 1
    else:
        scene.image_bytes = image_bytes


def tab_create_images() -> None:
    st.subheader("Create images")
    st.caption("Generate images per scene. Increase variations as needed.")

    if not st.session_state.scenes:
        st.warning("Create scenes first.")
        return

    variations = st.slider("Variations per scene", 1, 4, 1)
    per_scene = st.checkbox("Generate 1 image per scene (recommended)", value=True)

    if st.button("Generate images for all scenes", type="primary", use_container_width=True):
        with st.spinner("Generating images..."):
            for i, sc in enumerate(st.session_state.scenes):
                sid = get_scene_key(sc, i)
                sc.image_prompt = st.session_state.scene_prompts.get(sid, sc.image_prompt)
                sc.image_variations = []
                sc.image_bytes = None

                count = 1 if per_scene else variations
                for _ in range(count):
                    updated = generate_image_for_scene(
                        sc,
                        aspect_ratio=st.session_state.aspect_ratio,
                        visual_style=st.session_state.visual_style,
                    )
                    if updated.image_bytes:
                        _store_scene_image(sc, updated.image_bytes)
        st.toast("Image generation complete.")

    st.divider()

    for i, sc in enumerate(st.session_state.scenes):
        sid = get_scene_key(sc, i)
        primary = _get_primary_image(sc)

        with st.expander(f"{i + 1:02d} — {scene_title(sc, i)} images", expanded=False):
            if primary:
                st.image(primary, use_container_width=True)
            else:
                st.info("No images yet for this scene.")

            if sc.image_variations:
                with st.expander("Variations"):
                    st.image([img for img in sc.image_variations if img], use_container_width=True)

            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Regenerate this scene", key=f"regen_{sid}"):
                    sc.image_prompt = st.session_state.scene_prompts.get(sid, sc.image_prompt)
                    with st.spinner("Regenerating..."):
                        sc.image_variations = []
                        sc.image_bytes = None
                        for _ in range(variations):
                            updated = generate_image_for_scene(
                                sc,
                                aspect_ratio=st.session_state.aspect_ratio,
                                visual_style=st.session_state.visual_style,
                            )
                            if updated.image_bytes:
                                _store_scene_image(sc, updated.image_bytes)
                    st.toast("Regenerated.")
                    st.rerun()
            with c2:
                st.caption("Tip: prompts live in the Prompts tab.")


def tab_export_package() -> None:
    st.subheader("Export package")
    st.caption("Bundle script + scenes + prompts + images into a downloadable package.")

    if not st.session_state.script_text.strip():
        st.warning("No script to export.")
        return
    if not st.session_state.scenes:
        st.warning("No scenes to export.")
        return

    st.markdown("### Export preview")
    st.write(f"**Project:** {st.session_state.active_story_title}")
    st.write(f"**Scenes:** {len(st.session_state.scenes)}")

    include_all_variations = st.checkbox("Include all image variations", value=False)
    include_voiceover = st.checkbox("Include narration voiceover", value=False)
    voice_id = st.text_input("ElevenLabs voice ID", value=st.session_state.get("voice_id", ""))

    if st.button("Build ZIP", type="primary", use_container_width=True):
        voiceover_bytes = None
        if include_voiceover:
            with st.spinner("Generating voiceover..."):
                voiceover_bytes, error = generate_voiceover(
                    st.session_state.script_text,
                    voice_id=voice_id,
                )
                if error:
                    st.warning(error)
                    voiceover_bytes = None

        zip_bytes = build_export_zip(
            st.session_state.script_text,
            st.session_state.scenes,
            include_all_variations=include_all_variations,
            voiceover_bytes=voiceover_bytes,
        )
        st.download_button(
            "Download ZIP",
            data=zip_bytes,
            file_name="history_forge_export.zip",
            mime="application/zip",
            use_container_width=True,
        )



def main() -> None:
    st.set_page_config(page_title="The History Forge", layout="wide")
    require_login()
    init_state()

    tabs = st.tabs(
        [
            "Paste Script",
            "Generate Script",
            "Create Scenes",
            "Create Prompts",
            "Create Images",
            "Export Package",
        ]
    )

    with tabs[0]:
        tab_paste_script()
    with tabs[1]:
        tab_generate_script()
    with tabs[2]:
        tab_create_scenes()
    with tabs[3]:
        tab_create_prompts()
    with tabs[4]:
        tab_create_images()
    with tabs[5]:
        tab_export_package()


if __name__ == "__main__":
    main()
