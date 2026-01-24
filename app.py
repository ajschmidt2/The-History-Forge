import streamlit as st
from typing import List
from datetime import datetime
import zipfile
import io
import json
import random

LUCKY_TOPICS = [
    "The day a volcano ended a civilization overnight",
    "A forgotten battle decided by weather, not soldiers",
    "The real story behind the Dancing Plague of 1518",
    "A spy who changed the outcome of a war with one message",
    "A ship that vanished and reappeared decades later (what really happened)",
    "The lost library that may have contained the ancient world’s greatest knowledge",
    "The con artist who fooled kings and started a scandal",
    "The ‘unknown’ invention that quietly changed modern life",
    "A siege where defenders used an impossible trick to survive",
    "A single wrong turn that rewrote an empire’s future",
]


from utils import (
    Scene,
    generate_script,
    split_script_into_scenes,
    generate_prompts_for_scenes,
    generate_image_for_scene,
)

def require_login() -> None:
    pw = st.secrets.get("app_password", "").strip()
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

def build_export_zip(script: str, scenes: List[Scene]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("script.txt", script or "")
        z.writestr("scenes.json", json.dumps([s.to_dict() for s in scenes], indent=2))
        for s in scenes:
            if s.image_variations:
                # Export primary image
                z.writestr(
                    f"images/scene_{s.index:02d}_primary.png",
                    s.image_bytes,
                )

                # Export all variations
                for i, img_bytes in enumerate(s.image_variations, start=1):
                    z.writestr(
                        f"images/scene_{s.index:02d}_var_{i}.png",
                        img_bytes,
                    )

    return buf.getvalue()

def main() -> None:
    st.set_page_config(page_title="The History Forge", layout="wide")
    require_login()

    st.title("🔥 The History Forge")
    st.caption("Generate a YouTube history script + scenes + prompts + images.")

    st.sidebar.subheader("🧠 Topic")
    topic_col1, topic_col2 = st.sidebar.columns([3, 2])
    with topic_col1:
        topic = st.text_input(
            "Put your own topic",
            value=st.session_state.get("topic", ""),
            placeholder="e.g., The mystery of Alexander the Great’s tomb",
            label_visibility="collapsed",
        )
    with topic_col2:
        if st.button("🍀 I’m feeling lucky", use_container_width=True):
            import random
            picked = random.choice(LUCKY_TOPICS)
            st.session_state.topic = picked
            st.rerun()
    
    # Ensure we always have something
    if not topic.strip():
        topic = st.session_state.get("topic", "The mystery of Alexander the Great's tomb")

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

    num_scenes = st.sidebar.slider(
        "Number of scenes (images = scenes)",
        min_value=1,
        max_value=60,   # bump higher if you want
        value=10,
        step=1,
        help="Creates one image per scene by default."
    )

    variations_per_scene = st.sidebar.selectbox(
    "Image variations per scene",
    [1, 2],
    index=0,
    help="2 variations doubles image generation time and quota usage."
    )

    st.sidebar.divider()
    if st.sidebar.button("🧹 Reset app state (use after redeploy)", use_container_width=True):
        for k in ["script", "scenes", "topic", "authenticated", "script_editor"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    st.sidebar.divider()
    generate_all = st.sidebar.button("✨ Generate Package", type="primary", use_container_width=True)
    debug_mode = st.sidebar.toggle("Debug mode", value=True)

    if generate_all:
        st.session_state.topic = topic

        with st.status("Generating…", expanded=True) as status:
            status.update(label="1/4 Writing script…")
            script = generate_script(topic=topic, length=length, tone=tone)
            st.session_state.script = script

            status.update(label=f"2/4 Splitting into {num_scenes} scenes…")
            scenes = split_script_into_scenes(script, max_scenes=num_scenes)
            st.session_state.scenes = scenes

            status.update(label="3/4 Writing prompts…")
            scenes = generate_prompts_for_scenes(scenes, tone=tone, style=visual_style)
            st.session_state.scenes = scenes

            status.update(label="4/4 Generating images…")

            scenes_out = []
            failed = 0
            
            for s in scenes:
                # clear prior variations for a clean run
                s.image_variations = []
                s.primary_image_idx = 0
            
                for v in range(variations_per_scene):
                    s = generate_image_for_scene(
                        s,
                        aspect_ratio=aspect_ratio,
                        visual_style=visual_style,
                    )
                    if not s.image_variations or len(s.image_variations) < (v + 1):
                        failed += 1
            
                scenes_out.append(s)
            
            # Optional: one retry pass for failures
            if failed:
                status.update(label=f"4/4 Retrying some failed images…")
                for s in scenes_out:
                    # retry only scenes missing any image
                    if not s.image_variations:
                        for v in range(variations_per_scene):
                            s = generate_image_for_scene(s, aspect_ratio=aspect_ratio, visual_style=visual_style)
            
            st.session_state.scenes = scenes_out
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
        scenes: List[Scene] = st.session_state.get("scenes", [])
        if not scenes:
            st.info("Generate a package to see scenes and images here.")
        else:
            st.caption(f"Scenes generated: {len(scenes)} (target: {num_images})")
            for s in scenes:
                with st.expander(f"Scene {s.index}: {s.title}", expanded=(s.index == 1)):
                    st.markdown("**Scene excerpt**")
                    st.write(s.script_excerpt or "—")

                    st.markdown("**Visual intent**")
                    st.write(s.visual_intent or "—")

                    st.markdown("**Image prompt**")
                    st.code(s.image_prompt or "—", language="text")

                    if s.image_variations:
                        st.markdown("**Variations**")
                        cols = st.columns(min(4, len(s.image_variations)))
                        for i, img_b in enumerate(s.image_variations):
                            with cols[i % len(cols)]:
                                st.image(img_b, use_container_width=True)
                                if st.button("Set primary", key=f"primary_{s.index}_{i}", use_container_width=True):
                                    s.primary_image_idx = i
                                    st.session_state.scenes = scenes
                                    st.rerun()
                    
                        st.markdown("**Selected (primary) image**")
                        st.image(s.image_bytes, caption=f"Scene {s.index} primary", use_container_width=True)
                    else:
                        st.error("No image generated for this scene.")

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
                        if st.button("🔄 Regenerate image", key=f"regen_{s.index}", use_container_width=True):
                            try:
                                updated = generate_image_for_scene(
                                    s,
                                    aspect_ratio=aspect_ratio,
                                    visual_style=visual_style,
                                )
                                for i in range(len(scenes)):
                                    if scenes[i].index == s.index:
                                        scenes[i] = updated
                                        break
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
        scenes: List[Scene] = st.session_state.get("scenes", [])
        if not script or not scenes:
            st.info("Generate a package first.")
        else:
            zip_bytes = build_export_zip(script, scenes)
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
- optional: `app_password`

If images fail, check logs for:
- `[Gemini image gen failed]`
- `[Gemini image gen final] FAILED`
""".strip()
        )

if __name__ == "__main__":
    main()
