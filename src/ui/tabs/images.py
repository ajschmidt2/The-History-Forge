from pathlib import Path

import streamlit as st

from utils import Scene, generate_image_for_scene
from src.storage import record_asset
from src.ui.state import active_project_id, scenes_ready


def _save_scene_image_bytes(scene: Scene, image_bytes: bytes) -> None:
    scene.image_bytes = image_bytes
    scene.image_variations = [image_bytes]
    scene.primary_image_index = 0
    scene.image_error = ""

    images_dir = Path("data/projects") / active_project_id() / "assets/images"
    images_dir.mkdir(parents=True, exist_ok=True)
    destination = images_dir / f"s{scene.index:02d}.png"
    destination.write_bytes(image_bytes)
    record_asset(active_project_id(), "image", destination)


def tab_create_images() -> None:
    st.subheader("Create images")

    if not scenes_ready():
        st.warning("Create scenes first.")
        return

    st.info(
        "You can generate images with AI, upload your own image for each scene, or bulk upload scene images."
    )

    aspect_ratio_options = ["16:9", "9:16", "1:1"]
    current_aspect_ratio = (
        st.session_state.aspect_ratio
        if st.session_state.aspect_ratio in aspect_ratio_options
        else aspect_ratio_options[0]
    )
    st.session_state.aspect_ratio = st.selectbox(
        "Aspect ratio",
        aspect_ratio_options,
        index=aspect_ratio_options.index(current_aspect_ratio),
    )
    st.session_state.variations_per_scene = st.slider(
        "Variations per scene",
        1,
        4,
        int(st.session_state.variations_per_scene),
    )

    bulk_uploads = st.file_uploader(
        "Bulk upload scene images (optional, ordered by scene number)",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="bulk_scene_image_upload",
        help="When uploaded, files are assigned to scenes in order: first file -> Scene 1, second -> Scene 2, etc.",
    )
    if bulk_uploads:
        applied = 0
        for scene, upload in zip(st.session_state.scenes, bulk_uploads):
            _save_scene_image_bytes(scene, upload.getvalue())
            applied += 1
        st.success(f"Applied {applied} uploaded image(s) to scenes and saved them to assets/images.")
        st.rerun()

    if st.button("Generate images for all scenes", type="primary", width="stretch"):
        scene_failures: list[str] = []
        generated_count = 0
        cache: dict[str, bytes] = st.session_state.get("generated_image_cache", {})
        with st.spinner("Generating images..."):
            for s in st.session_state.scenes:
                if not (s.image_prompt or "").strip():
                    s.image_prompt = f"Create a cinematic historical visual for: {s.title}."

                s.image_variations = []
                s.image_error = ""
                for variation_index in range(int(st.session_state.variations_per_scene)):
                    cache_key = (
                        f"{(s.image_prompt or '').strip()}|{st.session_state.aspect_ratio}|"
                        f"{st.session_state.visual_style}|{variation_index}"
                    )
                    cached_bytes = cache.get(cache_key)
                    if cached_bytes:
                        s.image_variations.append(cached_bytes)
                        continue
                    try:
                        updated = generate_image_for_scene(
                            s,
                            aspect_ratio=st.session_state.aspect_ratio,
                            visual_style=st.session_state.visual_style,
                        )
                    except Exception as exc:  # noqa: BLE001 - keep per-scene error handling resilient
                        s.image_error = f"Image generation failed: {exc}"
                        scene_failures.append(f"Scene {s.index:02d}")
                        break
                    if updated.image_bytes:
                        cache[cache_key] = updated.image_bytes
                        s.image_variations.append(updated.image_bytes)

                s.primary_image_index = 0
                s.image_bytes = s.image_variations[0] if s.image_variations else None
                if s.image_bytes:
                    _save_scene_image_bytes(s, s.image_bytes)
                    generated_count += 1

        st.session_state.generated_image_cache = cache

        if scene_failures:
            st.warning(
                f"Generated images for {generated_count} scene(s). Failed: {', '.join(scene_failures)}."
            )
        else:
            st.toast("Image generation complete. Images auto-saved to assets/images.")
        st.rerun()

    st.divider()

    for s in st.session_state.scenes:
        with st.expander(f"{s.index:02d} — {s.title} images", expanded=False):
            if s.image_bytes:
                st.image(s.image_bytes, width="stretch")
            else:
                st.info("No primary image yet.")

            uploaded_scene_image = st.file_uploader(
                f"Upload your own image for scene {s.index:02d}",
                type=["png", "jpg", "jpeg"],
                key=f"scene_upload_{s.index}",
            )
            if uploaded_scene_image is not None:
                _save_scene_image_bytes(s, uploaded_scene_image.getvalue())
                st.success(f"Uploaded image applied to scene {s.index:02d}.")
                st.rerun()

            if len(s.image_variations) > 1:
                st.caption("Variations")
                for vi, b in enumerate(s.image_variations[1:], start=2):
                    if b:
                        st.image(b, caption=f"Variation {vi}", width="stretch")

            if s.image_error:
                st.error(s.image_error)

            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Regenerate this scene", key=f"regen_{s.index}", width="stretch"):
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
                        if s.image_bytes:
                            _save_scene_image_bytes(s, s.image_bytes)
                    st.toast("Regenerated.")
                    st.rerun()
            with c2:
                st.caption("Edit the prompt in the Prompts tab for better results.")
