import streamlit as st
from typing import List
from datetime import datetime
import zipfile
import io
import json
import random

from utils import (
    Scene,
    generate_script,
    split_script_into_scenes,
    generate_prompts_for_scenes,
    generate_image_for_scene,
    generate_voiceover,
    get_secret,
    generate_visuals_from_script,
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

def main() -> None:
    st.set_page_config(page_title="The History Forge", layout="wide")
    require_login()
    st.title("🔥 The History Forge")
    st.caption("Generate a YouTube history script + scenes + prompts + images.")

    st.sidebar.header("⚙️ Controls")
    curated_topics = [
        "The lost city of Atlantis and why it endures",
        "The mystery of Alexander the Great's tomb",
        "The night Pompeii vanished beneath ash",
        "The real story behind the Trojan War",
        "The rise and fall of the Library of Alexandria",
        "The secret tunnels of ancient Rome",
        "How the Black Death reshaped Europe",
        "The treasure of the Knights Templar",
        "The longest siege in medieval history",
        "The spy who saved D-Day",
        "The shipwreck that changed global trade",
        "The assassination that sparked World War I",
    ]
    def _set_lucky_topic():
        lucky_topic = random.choice(curated_topics)
        st.session_state["topic_input"] = lucky_topic
        st.session_state["topic"] = lucky_topic

    topic_default = st.session_state.get("topic", curated_topics[1])
    topic = st.sidebar.text_input("Topic", value=topic_default, key="topic_input")
    st.sidebar.button(
        "🎲 I'm Feeling Lucky",
        use_container_width=True,
        on_click=_set_lucky_topic,
    )
    length = st.sidebar.selectbox(
        "Length",
        ["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"],
        index=1
    )
    tone = st.sidebar.selectbox(
        "Tone",
        ["Cinematic", "Mysterious", "Educational", "Eerie"],
        index=0
    )
    aspect_ratio = st.sidebar.selectbox(
        "Image aspect ratio",
        ["16:9", "9:16", "1:1"],
        index=0
    )

    visual_style = st.sidebar.selectbox(
        "Image style",
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
        index=0
    )

    num_images = st.sidebar.slider(
        "Number of images to create",
        min_value=1,
        max_value=75,
        value=8,
        step=1,
        help="This sets how many scenes (and therefore how many images) are generated (max 75)."
    )
    variations_per_scene = st.sidebar.selectbox(
        "Variations per scene",
        [1, 2],
        index=0,
        help="Create multiple image options per scene."
    )
    st.sidebar.divider()
    enable_voiceover = st.sidebar.toggle("Generate narration voiceover", value=False)
    voice_id_default = st.session_state.get("voice_id", "r6YelDxIe1A40lDuW365")
    voice_id = st.sidebar.text_input("ElevenLabs voice ID", value=voice_id_default)

    st.sidebar.divider()
    if st.sidebar.button("🧹 Reset app state (use after redeploy)", use_container_width=True):
        for k in ["script", "scenes", "topic", "authenticated", "script_editor", "pasted_script"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    st.sidebar.divider()
    generate_all = st.sidebar.button("✨ Generate Package", type="primary", use_container_width=True)
    split_pasted_script = st.sidebar.button("🧩 Split pasted script into scenes", use_container_width=True)
    generate_paste_prompts = st.sidebar.button("📝 Create prompts from scenes", use_container_width=True)
    generate_paste_images = st.sidebar.button("🎨 Generate images from prompts", use_container_width=True)
    debug_mode = st.sidebar.toggle("Debug mode", value=True)

    with st.sidebar.expander("📄 Paste your script (optional)"):
        pasted_script = st.text_area(
            "Paste a narration script here to generate scenes + images without rewriting it.",
            value=st.session_state.get("pasted_script", ""),
            height=200,
            key="pasted_script_input",
        )
        if st.button("Use pasted script", use_container_width=True):
            st.session_state.pasted_script = pasted_script
            st.session_state.script = pasted_script
            st.session_state.visuals_script = pasted_script
            st.success("Pasted script loaded. Now split into scenes.")

    if generate_all:
        st.session_state.topic = topic

        with st.status("Generating…", expanded=True) as status:
            status.update(label="1/5 Writing script…")
            script = generate_script(topic=topic, length=length, tone=tone)
            st.session_state.script = script
            st.session_state.visuals_script = script
            st.session_state.voice_id = voice_id

            status.update(label=f"2/5 Splitting into {num_images} scenes…")
            st.session_state.scenes = split_script_into_scenes(script, max_scenes=num_images)

            status.update(label="3/5 Writing prompts…")
            st.session_state.scenes = generate_prompts_for_scenes(
                st.session_state.scenes,
                tone=tone,
                style=visual_style,
            )

            status.update(label="4/5 Generating images…")
            scenes_out, failures = generate_visuals_from_script(
                script=script,
                num_images=num_images,
                tone=tone,
                visual_style=visual_style,
                aspect_ratio=aspect_ratio,
                variations_per_scene=variations_per_scene,
                scenes=st.session_state.scenes,
            )
            st.session_state.scenes = scenes_out
            if enable_voiceover:
                status.update(label="5/5 Generating voiceover…")
                voiceover_bytes, voiceover_error = generate_voiceover(
                    script=script,
                    voice_id=voice_id,
                    output_format="mp3",
                )
                st.session_state.voiceover_bytes = voiceover_bytes
                st.session_state.voiceover_error = voiceover_error

            if failures:
                status.update(label=f"Done (with {failures} image failures) ⚠️", state="complete")
            else:
                status.update(label="Done ✅", state="complete")

        st.session_state.story_settings = {
            "topic": topic,
            "length": length,
            "tone": tone,
            "aspect_ratio": aspect_ratio,
            "visual_style": visual_style,
            "num_images": num_images,
            "variations_per_scene": variations_per_scene,
            "script": script,
            "voice_id": voice_id,
        }

    if split_pasted_script:
        script = (
            st.session_state.get("pasted_script_input", "")
            or st.session_state.get("pasted_script", "")
        ).strip()
        if not script:
            st.sidebar.error("Paste a script first.")
        else:
            st.session_state.topic = "Pasted script"
            st.session_state.script = script
            st.session_state.visuals_script = script
            with st.status("Generating…", expanded=True) as status:
                status.update(label=f"1/1 Splitting pasted script into {num_images} scenes…")
                st.session_state.scenes = split_script_into_scenes(script, max_scenes=num_images)
                status.update(label="Scenes ready ✅", state="complete")

    if generate_paste_prompts:
        script = (
            st.session_state.get("visuals_script", "")
            or st.session_state.get("pasted_script_input", "")
            or st.session_state.get("pasted_script", "")
        ).strip()
        scenes: List[Scene] = st.session_state.get("scenes", [])
        if not script:
            st.sidebar.error("Paste a script first.")
        elif not scenes:
            st.sidebar.error("Split the script into scenes first.")
        else:
            with st.status("Generating…", expanded=True) as status:
                status.update(label="Writing prompts…")
                st.session_state.scenes = generate_prompts_for_scenes(
                    scenes,
                    tone=tone,
                    style=visual_style,
                )
                status.update(label="Prompts ready ✅", state="complete")

    if generate_paste_images:
        script = (
            st.session_state.get("visuals_script", "")
            or st.session_state.get("pasted_script_input", "")
            or st.session_state.get("pasted_script", "")
        ).strip()
        scenes: List[Scene] = st.session_state.get("scenes", [])
        missing_prompts = [s for s in scenes if not s.image_prompt]
        if not script:
            st.sidebar.error("Paste a script first.")
        elif not scenes:
            st.sidebar.error("Split the script into scenes first.")
        elif missing_prompts:
            st.sidebar.error("Generate prompts before making images.")
        else:
            with st.status("Generating…", expanded=True) as status:
                status.update(label="Generating images…")
                scenes_out, failures = generate_visuals_from_script(
                    script=script,
                    num_images=num_images,
                    tone=tone,
                    visual_style=visual_style,
                    aspect_ratio=aspect_ratio,
                    variations_per_scene=variations_per_scene,
                    scenes=scenes,
                )
                st.session_state.scenes = scenes_out

                if failures:
                    status.update(label=f"Done (with {failures} image failures) ⚠️", state="complete")
                else:
                    status.update(label="Done ✅", state="complete")

    tab_script, tab_visuals, tab_export = st.tabs(["📝 Script", "🖼️ Scenes & Visuals", "⬇️ Export"])

    with tab_script:
        st.subheader("Narration Script")
        script = st.session_state.get("script", "")
        if not script:
            st.info("Click **Generate Package** to create a script.")
        else:
            st.text_area("Script (editable)", value=script, height=420, key="script_editor")
            if st.button("💾 Save script edits", use_container_width=True):
                st.session_state.script = st.session_state.script_editor
                st.success("Saved.")

    with tab_visuals:
        st.subheader("Scenes & Visuals")
        visuals_script = st.session_state.get("visuals_script") or st.session_state.get("script", "")
        if visuals_script:
            with st.expander("Script used for image generation", expanded=True):
                st.text_area(
                    "Visuals script (read-only)",
                    value=visuals_script,
                    height=220,
                    disabled=True,
                )
        scenes: List[Scene] = sorted(st.session_state.get("scenes", []), key=lambda s: s.index)
        if not scenes:
            st.info("Generate a package to see scenes and images here.")
        else:
            show_deleted = st.toggle("Show deleted scenes", value=False)
            visible_scenes = [s for s in scenes if show_deleted or s.status != "deleted"]
            st.caption(f"Scenes generated: {len(visible_scenes)} (target: {num_images})")
            for s in visible_scenes:
                with st.expander(f"Scene {s.index}: {s.title}", expanded=(s.index == 1)):
                    action_cols = st.columns([1, 1, 1, 1])
                    with action_cols[0]:
                        if st.button("⬆️ Move up", key=f"up_{s.index}", use_container_width=True, disabled=s.index == 1):
                            scenes[s.index - 2], scenes[s.index - 1] = scenes[s.index - 1], scenes[s.index - 2]
                            _sync_scene_order(scenes)
                            st.session_state.scenes = scenes
                            st.rerun()
                    with action_cols[1]:
                        if st.button(
                            "⬇️ Move down",
                            key=f"down_{s.index}",
                            use_container_width=True,
                            disabled=s.index == len(scenes),
                        ):
                            scenes[s.index - 1], scenes[s.index] = scenes[s.index], scenes[s.index - 1]
                            _sync_scene_order(scenes)
                            st.session_state.scenes = scenes
                            st.rerun()
                    with action_cols[2]:
                        if st.button(
                            "🗑️ Delete scene",
                            key=f"delete_{s.index}",
                            use_container_width=True,
                            disabled=s.status == "deleted",
                        ):
                            s.status = "deleted"
                            st.session_state.scenes = scenes
                            st.rerun()
                    with action_cols[3]:
                        if st.button(
                            "↩️ Undo delete",
                            key=f"undo_{s.index}",
                            use_container_width=True,
                            disabled=s.status != "deleted",
                        ):
                            s.status = "active"
                            st.session_state.scenes = scenes
                            st.rerun()

                    st.markdown("**Scene excerpt**")
                    st.write(s.script_excerpt or "—")

                    st.markdown("**Visual intent**")
                    st.write(s.visual_intent or "—")

                    st.markdown("**Image prompt**")
                    st.code(s.image_prompt or "—", language="text")

                    primary = _get_primary_image(s)
                    if primary:
                        st.image(primary, caption=f"Scene {s.index} ({aspect_ratio})", width=200)
                        enlarge_key = f"show_primary_{s.index}"
                        if st.button("Enlarge", key=f"enlarge_{s.index}"):
                            st.session_state[enlarge_key] = True
                        if st.session_state.get(enlarge_key):
                            st.image(
                                primary,
                                caption=f"Scene {s.index} ({aspect_ratio})",
                                use_container_width=True,
                            )
                    else:
                        msg = "Image missing for this scene."
                        if s.image_error:
                            msg = f"{msg} {s.image_error}"
                        else:
                            msg = f"{msg} Check logs for '[Gemini image gen failed]'."
                        st.error(msg)

                    if s.image_variations and len(s.image_variations) > 1:
                        st.markdown("**Variations**")
                        selected_key = f"selected_variation_{s.index}"
                        if selected_key not in st.session_state:
                            st.session_state[selected_key] = s.primary_image_index

                        cols = st.columns(min(len(s.image_variations), 4))
                        for idx, img in enumerate(s.image_variations):
                            with cols[idx % len(cols)]:
                                if img:
                                    st.image(img, caption=f"Variation {idx + 1}", width=160)
                                    if st.button(
                                        "View larger",
                                        key=f"view_{s.index}_{idx}",
                                        use_container_width=True,
                                    ):
                                        st.session_state[selected_key] = idx
                                else:
                                    st.warning(f"Variation {idx + 1} missing.")
                                if st.button(
                                    "Set as primary",
                                    key=f"primary_{s.index}_{idx}",
                                    use_container_width=True,
                                    disabled=idx == s.primary_image_index,
                                ):
                                    s.primary_image_index = idx
                                    s.image_bytes = img
                                    st.session_state.scenes = scenes
                                    st.rerun()

                        selected_idx = st.session_state.get(selected_key, s.primary_image_index)
                        if 0 <= selected_idx < len(s.image_variations):
                            selected_img = s.image_variations[selected_idx]
                            if selected_img:
                                st.image(
                                    selected_img,
                                    caption=f"Selected variation {selected_idx + 1}",
                                    use_container_width=True,
                                )

                    refine = st.text_input(
                        f"Refine prompt (Scene {s.index})",
                        value="",
                        key=f"refine_{s.index}",
                        placeholder="e.g., tighter close-up, warmer lighting, more fog…",
                    )

                    c1, c2 = st.columns([1, 1])
                    with c1:
                        if st.button("✏️ Apply refinement", key=f"apply_ref_{s.index}", use_container_width=True):
                            if refine.strip():
                                s.image_prompt = (s.image_prompt + "\n\nRefinement: " + refine.strip()).strip()
                                st.success("Prompt updated. Now regenerate the image.")
                                st.rerun()

                    with c2:
                        if st.button("🔄 Regenerate primary image", key=f"regen_{s.index}", use_container_width=True):
                            try:
                                updated = generate_image_for_scene(
                                    s,
                                    aspect_ratio=aspect_ratio,
                                    visual_style=visual_style,
                                )
                                if s.image_variations:
                                    s.image_variations[s.primary_image_index] = updated.image_bytes
                                s.image_bytes = updated.image_bytes
                                st.session_state.scenes = scenes
                                if updated.image_bytes:
                                    st.success("Image regenerated.")
                                else:
                                    st.error("Regeneration failed (no bytes returned). Check logs.")
                                st.rerun()
                            except Exception as e:
                                st.error("Image regeneration failed.")
                                if debug_mode:
                                    st.exception(e)

    with tab_export:
        st.subheader("Export")
        script = st.session_state.get("script", "")
        scenes: List[Scene] = sorted(st.session_state.get("scenes", []), key=lambda s: s.index)
        voiceover_bytes = st.session_state.get("voiceover_bytes") if enable_voiceover else None
        voiceover_error = st.session_state.get("voiceover_error") if enable_voiceover else None
        if not script or not scenes:
            st.info("Generate a package first.")
        else:
            if enable_voiceover:
                if voiceover_bytes:
                    st.audio(voiceover_bytes, format="audio/mp3")
                elif voiceover_error:
                    st.error(f"Voiceover failed: {voiceover_error}")
            export_mode = st.radio(
                "Image export options",
                ["Primary images only", "All variations"],
                horizontal=True,
            )
            include_all_variations = export_mode == "All variations"
            zip_bytes = build_export_zip(script, scenes, include_all_variations, voiceover_bytes)
            st.download_button(
                "⬇️ Download Package (ZIP)",
                data=zip_bytes,
                file_name=f"history_forge_{datetime.now().strftime('%Y%m%d_%H%M')}.zip",
                use_container_width=True,
            )

    with st.sidebar.expander("ℹ️ Secrets checklist"):
        st.markdown(
            """
**Streamlit Cloud → Secrets**
- `openai_api_key`
- `gemini_api_key`
- `elevenlabs_api_key`
- optional: `APP_PASSCODE` (or legacy `app_password`)

If images fail, check logs for:
- `[Gemini image gen failed]`
- `[Gemini image gen final] FAILED`
""".strip()
        )

if __name__ == "__main__":
    main()
