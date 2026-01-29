import json
import zipfile
from io import BytesIO

import streamlit as st

from utils import (
    Scene,
    generate_lucky_topic,
    generate_script,
    split_script_into_scenes,
    generate_prompts_for_scenes,
    generate_image_for_scene,
    generate_voiceover,
)


# ----------------------------
# Auth gate (uses Streamlit secrets)
# ----------------------------

def require_passcode() -> None:
    secret_key = "APP_PASSCODE" if "APP_PASSCODE" in st.secrets else "password"
    expected = st.secrets.get(secret_key, "")

    if not expected:
        return

    st.session_state.setdefault("auth_ok", False)
    if st.session_state.auth_ok:
        return

    st.title("🔒 The History Forge")
    code = st.text_input("Password", type="password")
    if st.button("Log in", type="primary"):
        st.session_state.auth_ok = code == expected
        if not st.session_state.auth_ok:
            st.error("Incorrect password.")
        st.rerun()
    st.stop()


# ----------------------------
# State
# ----------------------------

def init_state() -> None:
    st.session_state.setdefault("project_title", "Untitled Project")
    st.session_state.setdefault("topic", "")
    st.session_state.setdefault("script_text", "")

    st.session_state.setdefault("tone", "Documentary")
    st.session_state.setdefault("length", "8–10 minutes")

    st.session_state.setdefault("visual_style", "Photorealistic cinematic")
    st.session_state.setdefault("aspect_ratio", "16:9")
    st.session_state.setdefault("variations_per_scene", 1)

    st.session_state.setdefault("max_scenes", 12)
    st.session_state.setdefault("scenes", [])

    st.session_state.setdefault("voice_id", "")
    st.session_state.setdefault("voiceover_bytes", None)
    st.session_state.setdefault("voiceover_error", None)


def scenes_ready() -> bool:
    return isinstance(st.session_state.scenes, list) and len(st.session_state.scenes) > 0


def script_ready() -> bool:
    return bool((st.session_state.script_text or "").strip())


def clear_downstream(after: str) -> None:
    """
    Clear downstream artifacts when upstream changes.
    after = "script" clears scenes/prompts/images/voiceover.
    after = "scenes" clears prompts/images.
    after = "prompts" clears images.
    """
    if after in ("script",):
        st.session_state.scenes = []
        st.session_state.voiceover_bytes = None
        st.session_state.voiceover_error = None

    if after in ("script", "scenes"):
        if isinstance(st.session_state.scenes, list):
            for s in st.session_state.scenes:
                if isinstance(s, Scene):
                    s.image_prompt = ""
                    s.image_bytes = None
                    s.image_variations = []
                    s.primary_image_index = 0
                    s.image_error = ""

    if after in ("script", "scenes", "prompts"):
        if isinstance(st.session_state.scenes, list):
            for s in st.session_state.scenes:
                if isinstance(s, Scene):
                    s.image_bytes = None
                    s.image_variations = []
                    s.primary_image_index = 0
                    s.image_error = ""


# ----------------------------
# Tabs
# ----------------------------

def tab_paste_script() -> None:
    st.subheader("Paste your own script")

    st.session_state.project_title = st.text_input(
        "Project Title",
        value=st.session_state.project_title,
        placeholder="e.g., The Rise of Rome",
    )

    new_script = st.text_area(
        "Script",
        value=st.session_state.script_text,
        height=320,
        placeholder="Paste your narration script here...",
    )

    if st.button("Use this script →", type="primary", use_container_width=True):
        st.session_state.script_text = new_script
        clear_downstream("script")
        st.toast("Script loaded.")
        st.rerun()


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
        if st.button("🎲 I'm Feeling Lucky", use_container_width=True):
            st.session_state.topic = generate_lucky_topic()
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

    if st.button("Generate Script", type="primary", use_container_width=True):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic or use I'm Feeling Lucky.")
            return
        with st.spinner("Generating script..."):
            st.session_state.script_text = generate_script(
                topic=st.session_state.topic,
                length=st.session_state.length,
                tone=st.session_state.tone,
            )
        st.session_state.project_title = st.session_state.topic or st.session_state.project_title
        clear_downstream("script")
        st.toast("Script generated.")
        st.rerun()

    if script_ready():
        with st.expander("Preview script", expanded=False):
            st.write(st.session_state.script_text)


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

    if st.button("Split script into scenes", type="primary", use_container_width=True):
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
    for s in st.session_state.scenes:
        with st.expander(f"{s.index:02d} — {s.title}", expanded=False):
            s.title = st.text_input("Title", value=s.title, key=f"title_{s.index}")
            s.script_excerpt = st.text_area("Excerpt", value=s.script_excerpt, height=140, key=f"txt_{s.index}")
            s.visual_intent = st.text_area("Visual intent", value=s.visual_intent, height=90, key=f"vi_{s.index}")

    st.caption("Tip: prompts + images are generated in the next tabs.")


def tab_create_prompts() -> None:
    st.subheader("Create prompts")

    if not scenes_ready():
        st.warning("Create scenes first.")
        return

    st.session_state.visual_style = st.selectbox(
        "Visual style",
        ["Photorealistic cinematic", "Painterly", "Vintage photo", "Illustrated"],
        index=0,
    )

    if st.button("Generate prompts for all scenes", type="primary", use_container_width=True):
        with st.spinner("Generating prompts..."):
            st.session_state.scenes = generate_prompts_for_scenes(
                st.session_state.scenes,
                tone=st.session_state.tone,
                style=st.session_state.visual_style,
            )
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


def tab_create_images() -> None:
    st.subheader("Create images")

    if not scenes_ready():
        st.warning("Create scenes first.")
        return

    st.session_state.aspect_ratio = st.selectbox("Aspect ratio", ["16:9", "9:16", "1:1"], index=0)
    st.session_state.variations_per_scene = st.slider(
        "Variations per scene",
        1,
        4,
        int(st.session_state.variations_per_scene),
    )

    if st.button("Generate images for all scenes", type="primary", use_container_width=True):
        with st.spinner("Generating images..."):
            for s in st.session_state.scenes:
                if not (s.image_prompt or "").strip():
                    s.image_prompt = f"Create a cinematic historical visual for: {s.title}."

                s.image_variations = []
                for _ in range(int(st.session_state.variations_per_scene)):
                    updated = generate_image_for_scene(
                        s,
                        aspect_ratio=st.session_state.aspect_ratio,
                        visual_style=st.session_state.visual_style,
                    )
                    s.image_variations.append(updated.image_bytes)

                s.primary_image_index = 0
                s.image_bytes = s.image_variations[0] if s.image_variations else None

        st.toast("Image generation complete.")
        st.rerun()

    st.divider()

    for s in st.session_state.scenes:
        with st.expander(f"{s.index:02d} — {s.title} images", expanded=False):
            if s.image_bytes:
                st.image(s.image_bytes, use_container_width=True)
            else:
                st.info("No primary image yet.")

            if s.image_variations:
                st.caption("Variations")
                for vi, b in enumerate(s.image_variations):
                    if b:
                        st.image(b, caption=f"Variation {vi + 1}", use_container_width=True)

            if s.image_error:
                st.error(s.image_error)

            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Regenerate this scene", key=f"regen_{s.index}", use_container_width=True):
                    with st.spinner("Regenerating..."):
                        updated = generate_image_for_scene(
                            s,
                            aspect_ratio=st.session_state.aspect_ratio,
                            visual_style=st.session_state.visual_style,
                        )
                        s.image_bytes = updated.image_bytes
                        if s.image_variations:
                            s.image_variations[0] = updated.image_bytes
                        else:
                            s.image_variations = [updated.image_bytes]
                    st.toast("Regenerated.")
                    st.rerun()
            with c2:
                st.caption("Edit the prompt in the Prompts tab for better results.")


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

    if st.button("Generate voiceover", type="primary", use_container_width=True):
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
            st.toast("Voiceover generated.")
        st.rerun()

    if st.session_state.voiceover_error:
        st.error(st.session_state.voiceover_error)

    if st.session_state.voiceover_bytes:
        st.audio(st.session_state.voiceover_bytes, format="audio/mp3")


def build_zip() -> bytes:
    """
    Export: script.txt, scenes.json, images/*.png, voiceover.mp3 (if present)
    """
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
        use_container_width=True,
    )


def main() -> None:
    st.set_page_config(page_title="The History Forge", layout="wide")
    require_passcode()
    init_state()

    st.title("The History Forge")
    st.caption("Generate scripts, scene lists, prompts, images, and voiceover from a single workflow.")

    tabs = st.tabs(
        [
            "📝 Paste Script",
            "✨ Generate Script",
            "🧩 Scenes",
            "🧠 Prompts",
            "🖼️ Images",
            "🎙️ Voiceover",
            "📦 Export",
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
        tab_voiceover()
    with tabs[6]:
        tab_export()


if __name__ == "__main__":
    main()
