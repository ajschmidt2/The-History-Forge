"""History Video Generator (Streamlit).

Gemini-inspired, artifact-first UI:
* Script and visuals are treated as editable artifacts (not chat logs).
* Progressive status updates ("steps") build trust without exposing prompts.
* Per-scene regeneration and inline refinements reduce frustration.

Run:
    pip install -r requirements.txt
    streamlit run app.py
"""
import streamlit as st

def check_password():
    """Simple password protection"""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.title("🔒 The History Forge")
    st.write("Enter password to continue")

    password = st.text_input("Password", type="password")

    if st.button("Log in"):
        if password == st.secrets["app_password"]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")

    return False


if not check_password():
    st.stop()

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import streamlit as st

from utils import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_NUM_SCENES,
    SceneArtifact,
    compile_export_bundle,
    generate_images_for_scenes,
    generate_script,
    plan_scenes,
    refine_script,
    refine_scene_prompt,
)


APP_TITLE = "History Video Generator"


def _init_state() -> None:
    st.session_state.setdefault("script", "")
    st.session_state.setdefault("scenes", [])  # List[SceneArtifact]
    st.session_state.setdefault("last_topic", "")


def _sidebar_controls() -> dict:
    """All user controls live in the sidebar for a frictionless main canvas."""
    with st.sidebar:
        st.header("Controls")
        topic = st.text_input(
            "Topic / Title",
            placeholder="e.g., The Battle of Olustee — Florida’s Bloodiest Civil War Battle",
        )

        length_option = st.selectbox(
            "Length",
            [
                "Short (~60 seconds)",
                "Standard (8–10 minutes)",
                "Long (20–30 minutes)",
            ],
            index=1,
        )

        tone = st.selectbox(
            "Tone",
            ["Cinematic", "Mysterious", "Educational", "Eerie"],
            index=0,
        )

        st.divider()
        st.subheader("Visuals")
        aspect_ratio = st.selectbox(
            "Aspect ratio",
            ["16:9", "9:16", "1:1"],
            index=["16:9", "9:16", "1:1"].index(DEFAULT_ASPECT_RATIO),
        )

        visual_style = st.selectbox(
            "Style preset",
            [
                "Photorealistic cinematic",
                "Illustrated documentary",
                "Painterly",
                "Vintage archival",
            ],
            index=0,
        )

        num_scenes = st.slider("Number of scenes", 3, 12, DEFAULT_NUM_SCENES)
        generate_images = st.toggle("Generate images", value=True)

        st.divider()
        st.subheader("Quality")
        strict_accuracy = st.toggle(
            "Strict historical fidelity",
            value=True,
            help="When on, the planner avoids anachronisms and prefers neutral, plausible details.",
        )
        no_people = st.toggle(
            "No people",
            value=False,
            help="Useful for B-roll style visuals and avoiding faces.",
        )

        st.divider()
        generate_btn = st.button("Generate package", type="primary", use_container_width=True)

    return {
        "topic": topic,
        "length_option": length_option,
        "tone": tone,
        "aspect_ratio": aspect_ratio,
        "visual_style": visual_style,
        "num_scenes": num_scenes,
        "generate_images": generate_images,
        "strict_accuracy": strict_accuracy,
        "no_people": no_people,
        "generate_btn": generate_btn,
    }


def _generate_all(cfg: dict) -> None:
    """One-click generation with progressive status updates."""
    topic = (cfg.get("topic") or "").strip()
    if not topic:
        st.warning("Enter a topic/title in the sidebar to generate.")
        return

    status = st.status("Working…", expanded=True)
    try:
        status.write("Structuring story arc")
        script = generate_script(topic, cfg["length_option"], cfg["tone"], strict_accuracy=cfg["strict_accuracy"])

        st.session_state.script = script
        st.session_state.last_topic = topic

        status.update(label="Planning scenes", state="running")
        scenes = plan_scenes(
            script=script,
            topic=topic,
            tone=cfg["tone"],
            visual_style=cfg["visual_style"],
            aspect_ratio=cfg["aspect_ratio"],
            num_scenes=cfg["num_scenes"],
            strict_accuracy=cfg["strict_accuracy"],
            no_people=cfg["no_people"],
        )
        st.session_state.scenes = scenes

        if cfg["generate_images"]:
            status.update(label="Generating images", state="running")
            st.session_state.scenes = generate_images_for_scenes(scenes, aspect_ratio=cfg["aspect_ratio"])

        status.update(label="Done", state="complete")
    except Exception as e:
        status.update(label="Something went wrong", state="error")
        st.exception(e)


def _script_tab(cfg: dict) -> None:
    st.subheader("Narration Script")

    if not st.session_state.script:
        st.info("Generate a package to see the script here.")
        return

    st.text_area(
        "Script artifact",
        value=st.session_state.script,
        height=420,
        key="script_editor",
    )

    col1, col2 = st.columns([2, 3], gap="large")
    with col1:
        if st.button("Save edits to script", use_container_width=True):
            st.session_state.script = st.session_state.script_editor
            st.success("Saved.")

    with col2:
        refine_instruction = st.text_input(
            "Refine script (optional)",
            placeholder="e.g., Make the hook punchier and tighten the ending CTA.",
        )
        if st.button("Apply refinement", use_container_width=True) and refine_instruction.strip():
            with st.status("Refining script…", expanded=False):
                st.session_state.script = refine_script(
                    script=st.session_state.script,
                    instruction=refine_instruction.strip(),
                    tone=cfg["tone"],
                )
            st.success("Refined. (You can refine again.)")


def _visuals_tab(cfg: dict) -> None:
    st.subheader("Scenes & Visuals")
    scenes: list[SceneArtifact] = st.session_state.scenes

    if not scenes:
        st.info("Generate a package to see scenes and images here.")
        return

    for idx, sc in enumerate(scenes, start=1):
        with st.expander(f"Scene {idx}: {sc.title}", expanded=(idx == 1)):
            left, right = st.columns([2, 2], gap="large")
            with left:
                st.markdown("**Scene excerpt**")
                st.write(sc.script_excerpt)
                st.markdown("**Visual intent**")
                st.write(sc.visual_intent)

                st.markdown("**Image prompt**")
                st.code(sc.image_prompt, language="text")

                refine_p = st.text_input(
                    f"Refine prompt for Scene {idx}",
                    key=f"refine_prompt_{idx}",
                    placeholder="e.g., add more fog, stronger rim light, and keep era-accurate uniforms.",
                )
                btn_cols = st.columns(2)
                with btn_cols[0]:
                    if st.button("Apply prompt refinement", key=f"apply_prompt_{idx}", use_container_width=True) and refine_p.strip():
                        sc2 = refine_scene_prompt(
                            scene=sc,
                            instruction=refine_p.strip(),
                            tone=cfg["tone"],
                            visual_style=cfg["visual_style"],
                            aspect_ratio=cfg["aspect_ratio"],
                            strict_accuracy=cfg["strict_accuracy"],
                            no_people=cfg["no_people"],
                        )
                        scenes[idx - 1] = sc2
                        st.session_state.scenes = scenes
                        st.success("Prompt updated.")

                with btn_cols[1]:
                    if st.button("Regenerate image", key=f"regen_img_{idx}", use_container_width=True):
                        with st.status(f"Generating image for Scene {idx}…", expanded=False):
                            scenes[idx - 1] = generate_images_for_scenes([scenes[idx - 1]], aspect_ratio=cfg["aspect_ratio"])[0]
                            st.session_state.scenes = scenes
                        st.success("Image regenerated.")

            with right:
                if sc.image is None:
                    st.info("No image yet. Toggle ‘Generate images’ and run, or click Regenerate image.")
                else:
                    st.image(sc.image, caption=f"Scene {idx} image", use_container_width=True)


def _export_tab(cfg: dict) -> None:
    st.subheader("Export")

    if not st.session_state.script or not st.session_state.scenes:
        st.info("Generate a package first.")
        return

    bundle = compile_export_bundle(
        topic=st.session_state.last_topic or "history-video",
        script=st.session_state.script,
        scenes=st.session_state.scenes,
        meta={
            "tone": cfg["tone"],
            "length": cfg["length_option"],
            "aspect_ratio": cfg["aspect_ratio"],
            "visual_style": cfg["visual_style"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, data in bundle.items():
            zf.writestr(path, data)
    bio.seek(0)

    st.download_button(
        "Download ZIP package (script + prompts + images)",
        data=bio,
        file_name=f"{(st.session_state.last_topic or 'history-video').strip().replace(' ', '_')}_package.zip",
        mime="application/zip",
        use_container_width=True,
    )

    st.caption("Tip: This ZIP is editor-friendly: a script file, a JSON scene list, and per-scene PNG images.")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    _init_state()

    st.title("🎬 History Video Generator")
    st.caption("One click → narration script + scene-matched images. Artifact-first UI.")

    cfg = _sidebar_controls()
    if cfg["generate_btn"]:
        _generate_all(cfg)

    create_tab, script_tab, visuals_tab, export_tab = st.tabs(
        ["Create", "Script", "Visuals", "Export"]
    )

    with create_tab:
        st.subheader("Create")
        st.markdown(
            "Use the sidebar to set your topic, tone, length, and visuals—then click **Generate package**."
        )
        if st.session_state.last_topic:
            st.success(f"Latest package: {st.session_state.last_topic}")
        st.markdown("---")
        st.markdown("### What you get")
        st.markdown(
            "- A clean narration script (no stage directions)\n"
            "- Scene-by-scene breakdown\n"
            "- An image prompt per scene\n"
            "- Generated images (optional toggle)\n"
            "- Exportable ZIP for your editor"
        )

    with script_tab:
        _script_tab(cfg)

    with visuals_tab:
        _visuals_tab(cfg)

    with export_tab:
        _export_tab(cfg)


if __name__ == "__main__":
    main()
