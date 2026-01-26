import io
import json
import tempfile
import zipfile
from typing import Any, Dict, List

import streamlit as st

from utils import (
    Scene,
    generate_script,
    generate_lucky_topic,
    split_script_into_scenes,
    generate_prompts_for_scenes,
    generate_image_for_scene,
    generate_voiceover,
    rewrite_description,
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
    st.session_state.setdefault("script_text_pending", None)
    st.session_state.setdefault("scenes", [])
    st.session_state.setdefault("scene_prompts", {})
    st.session_state.setdefault("scene_images", {})
    st.session_state.setdefault("active_story_title", "Untitled Project")
    st.session_state.setdefault("aspect_ratio", "16:9")
    st.session_state.setdefault("visual_style", "Photorealistic cinematic")
    st.session_state.setdefault("voice_id", "r6YelDxIe1A40lDuW365")
    st.session_state.setdefault("voiceover_bytes", None)
    st.session_state.setdefault("voiceover_error", "")
    st.session_state.setdefault("compiled_video_bytes", None)
    st.session_state.setdefault("background_music_bytes", None)
    st.session_state.setdefault("background_music_name", "")
    st.session_state.setdefault("background_music_url", "")
    st.session_state.setdefault("background_music_error", "")
    st.session_state.setdefault("background_music_volume", 0.2)
    st.session_state.setdefault("title_options", [])
    st.session_state.setdefault("description_text", "")
    st.session_state.setdefault("thumbnail_prompt", "")
    st.session_state.setdefault("thumbnail_prompt_variations", [])
    st.session_state.setdefault("thumbnail_images", [])
    st.session_state.setdefault("thumbnail_aspect_ratio", "16:9")
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


def _ensure_scene_prompt(scene: Scene) -> str:
    if scene.image_prompt:
        return scene.image_prompt
    excerpt = (scene.script_excerpt or "").strip()
    visual = (scene.visual_intent or "").strip()
    base = "Create a cinematic historical visual aligned to the scene."
    prompt = f"{base}\n{visual}\nScene excerpt: {excerpt}".strip()
    scene.image_prompt = prompt
    return prompt


def tab_paste_script() -> None:
    st.subheader("Paste your own script")
    st.caption("Paste an existing script and use it as the source for scenes, prompts, images, and export.")

    pending_script = st.session_state.get("script_text_pending")
    if pending_script:
        st.session_state.script_text = pending_script
        st.session_state.script = pending_script
        st.session_state.script_text_pending = None

    st.text_area(
        "Script",
        value=st.session_state.script_text,
        height=320,
        placeholder="Paste your script here...",
        key="script_text",
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
    st.caption("Generate a script from a topic, or let ChatGPT surprise you with 'I'm Feeling Lucky'.")

    st.session_state.topic = st.text_input(
        "Topic",
        value=st.session_state.topic,
        placeholder="e.g., The Rise of Rome",
    )

    length_display = st.selectbox("Length", ["~1 min", "~3 min", "~5 min", "~10 min"], index=2)
    length_map = {
        "~1 min": "Short (~60 seconds)",
        "~3 min": "8–10 minutes",
        "~5 min": "8–10 minutes",
        "~10 min": "20–30 minutes",
    }
    tone = st.selectbox("Tone", ["Documentary", "Cinematic", "Mysterious", "Playful"], index=0)

    if st.button("🎲 I'm Feeling Lucky", use_container_width=True):
        with st.spinner("Finding a surprising story..."):
            st.session_state.topic = generate_lucky_topic()
            generated_script = generate_script(
                topic=st.session_state.topic,
                length=length_map[length_display],
                tone=tone,
            )
        st.session_state.script_text_pending = generated_script
        st.session_state.script = generated_script
        st.session_state.active_story_title = st.session_state.topic
        st.toast(f"Generated: {st.session_state.topic}")
        st.rerun()

    if st.button("Generate Script", type="primary", use_container_width=True):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic or use I'm Feeling Lucky.")
            return

        with st.spinner("Generating script..."):
            generated_script = generate_script(
                topic=st.session_state.topic,
                length=length_map[length_display],
                tone=tone,
            )
        st.session_state.script_text_pending = generated_script
        st.session_state.script = generated_script
        st.session_state.active_story_title = st.session_state.topic
        st.toast("Script generated.")
        st.rerun()

    preview_text = st.session_state.script_text_pending or st.session_state.script_text
    st.session_state["generated_script_preview"] = preview_text
    st.text_area(
        "Generated Script",
        value=preview_text,
        height=320,
        placeholder="Generated script will appear here...",
        key="generated_script_preview",
        disabled=True,
    )
    st.caption("Edit the script in the Paste Script tab.")


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
        style_options = [
            "Photorealistic cinematic",
            "Illustrated cinematic",
            "Painterly",
            "Comic / graphic novel",
            "Vintage archival photo",
            "3D render",
            "Watercolor illustration",
            "Charcoal / pencil sketch",
        ]
        current_style = st.session_state.get("visual_style", style_options[0])
        style_index = style_options.index(current_style) if current_style in style_options else 0
        st.session_state.visual_style = st.selectbox(
            "Visual style",
            style_options,
            index=style_index,
        )

    aspect_options = ["16:9", "9:16", "1:1"]
    current_aspect = st.session_state.get("aspect_ratio", aspect_options[0])
    aspect_index = aspect_options.index(current_aspect) if current_aspect in aspect_options else 0
    st.session_state.aspect_ratio = st.selectbox(
        "Image aspect ratio",
        aspect_options,
        index=aspect_index,
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

            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                if st.button("Move up", key=f"up_{key}", disabled=i == 0):
                    scenes = st.session_state.scenes
                    scenes[i - 1], scenes[i] = scenes[i], scenes[i - 1]
                    _sync_scene_order(scenes)
                    st.session_state.scenes = scenes
                    st.rerun()
            with c2:
                if st.button("Move down", key=f"down_{key}", disabled=i == len(st.session_state.scenes) - 1):
                    scenes = st.session_state.scenes
                    scenes[i + 1], scenes[i] = scenes[i], scenes[i + 1]
                    _sync_scene_order(scenes)
                    st.session_state.scenes = scenes
                    st.rerun()
            with c3:
                if st.button("Delete scene", key=f"del_{key}"):
                    st.session_state.scenes.pop(i)
                    _sync_scene_order(st.session_state.scenes)
                    st.rerun()


def tab_voiceover() -> None:
    st.subheader("Generate voiceover")
    st.caption("Create narration audio from your script using ElevenLabs.")

    if not st.session_state.script_text.strip():
        st.warning("Paste or generate a script first.")
        return

    st.session_state.voice_id = st.text_input(
        "ElevenLabs voice ID",
        value=st.session_state.get("voice_id", ""),
    )

    if st.button("Generate voiceover", type="primary", use_container_width=True):
        with st.spinner("Generating voiceover..."):
            voiceover_bytes, error = generate_voiceover(
                st.session_state.script_text,
                voice_id=st.session_state.voice_id,
            )
        st.session_state.voiceover_bytes = voiceover_bytes
        st.session_state.voiceover_error = error or ""
        if error:
            st.warning(error)
        else:
            st.success("Voiceover ready.")

    if st.session_state.voiceover_bytes:
        st.audio(st.session_state.voiceover_bytes, format="audio/mp3")
        st.download_button(
            "Download voiceover",
            data=st.session_state.voiceover_bytes,
            file_name="voiceover.mp3",
            mime="audio/mpeg",
            use_container_width=True,
        )


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
                st.session_state[f"prompt_{sid}"] = sc.image_prompt
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
            errors: List[str] = []
            missing_prompts = [s for s in st.session_state.scenes if not s.image_prompt]
            if missing_prompts:
                st.session_state.scenes = generate_prompts_for_scenes(
                    st.session_state.scenes,
                    tone="Cinematic",
                    style=st.session_state.visual_style,
                )
            for i, sc in enumerate(st.session_state.scenes):
                sid = get_scene_key(sc, i)
                sc.image_prompt = st.session_state.scene_prompts.get(sid, sc.image_prompt)
                _ensure_scene_prompt(sc)
                sc.image_variations = []
                sc.image_bytes = None

                count = 1 if per_scene else variations
                for _ in range(count):
                    updated = None
                    for attempt in range(2):
                        updated = generate_image_for_scene(
                            sc,
                            aspect_ratio=st.session_state.aspect_ratio,
                            visual_style=st.session_state.visual_style,
                        )
                        if updated.image_bytes:
                            _store_scene_image(sc, updated.image_bytes)
                            break
                        if updated.image_error and "no image bytes" not in updated.image_error.lower():
                            break
                    if updated and updated.image_error and not updated.image_bytes:
                        errors.append(f"{scene_title(sc, i)}: {updated.image_error}")
            if errors:
                st.warning("Image generation issues:\n" + "\n".join(errors))
        st.toast("Image generation complete.")

    st.divider()

    for i, sc in enumerate(st.session_state.scenes):
        sid = get_scene_key(sc, i)
        primary = _get_primary_image(sc)

        with st.expander(f"{i + 1:02d} — {scene_title(sc, i)} images", expanded=False):
            prompt_key = f"image_prompt_{sid}"
            augment_key = f"image_prompt_augment_{sid}"
            base_prompt = st.session_state.scene_prompts.get(sid, sc.image_prompt or "")
            st.session_state.setdefault(prompt_key, base_prompt)
            st.text_area(
                "Image prompt",
                key=prompt_key,
                height=90,
                placeholder="Edit the image prompt for this scene...",
            )
            st.session_state.scene_prompts[sid] = st.session_state.get(prompt_key, base_prompt)
            st.text_input(
                "Augment prompt",
                key=augment_key,
                placeholder="Optional additions (lighting, lens, mood, etc.)",
            )

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
                    base = st.session_state.get(prompt_key, "").strip()
                    augment = st.session_state.get(augment_key, "").strip()
                    combined_prompt = base
                    if augment:
                        combined_prompt = f"{base}\n\nAugment: {augment}".strip()
                    sc.image_prompt = combined_prompt
                    st.session_state.scene_prompts[sid] = base
                    with st.spinner("Regenerating..."):
                        sc.image_variations = []
                        sc.image_bytes = None
                        for _ in range(variations):
                            updated = None
                            for attempt in range(2):
                                updated = generate_image_for_scene(
                                    sc,
                                    aspect_ratio=st.session_state.aspect_ratio,
                                    visual_style=st.session_state.visual_style,
                                )
                                if updated.image_bytes:
                                    _store_scene_image(sc, updated.image_bytes)
                                    break
                                if updated.image_error and "no image bytes" not in updated.image_error.lower():
                                    break
                            if updated and updated.image_error and not updated.image_bytes:
                                st.warning(f"{scene_title(sc, i)}: {updated.image_error}")
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

    if st.button("Build ZIP", type="primary", use_container_width=True):
        voiceover_bytes = None
        if include_voiceover:
            voiceover_bytes = st.session_state.get("voiceover_bytes")
            if not voiceover_bytes:
                st.warning("Generate a voiceover in the Voiceover tab first.")

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



def _write_scene_images(scenes: List[Scene], temp_dir: str) -> List[str]:
    image_paths: List[str] = []
    for scene in scenes:
        img = _get_primary_image(scene)
        if not img:
            continue
        path = f"{temp_dir}/scene_{scene.index:02d}.png"
        with open(path, "wb") as f:
            f.write(img)
        image_paths.append(path)
    return image_paths


def _fetch_music_from_url(url: str) -> tuple[bytes | None, str]:
    if not url:
        return None, "Enter a URL to fetch."
    try:
        import requests
    except ModuleNotFoundError:
        return None, "Requests is not installed."
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        return None, f"Could not download audio: {exc}"
    content_type = response.headers.get("content-type", "").lower()
    if "audio" not in content_type and not url.lower().endswith((".mp3", ".wav", ".m4a", ".aac", ".ogg")):
        return None, "The URL does not appear to point to an audio file."
    return response.content, ""


def tab_compile_video() -> None:
    st.subheader("Compile slideshow video")
    st.caption("Combine scene images into an MP4 slideshow. Optionally attach the voiceover.")

    try:
        from PIL import Image
        if not hasattr(Image, "ANTIALIAS"):
            Image.ANTIALIAS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
        from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips, vfx
    except ModuleNotFoundError:
        st.error("MoviePy is not installed. Run `pip install -r requirements.txt` to enable video compilation.")
        return

    scenes = [s for s in st.session_state.scenes if s.status != "deleted"]
    if not scenes:
        st.warning("Create scenes and images first.")
        return

    duration_per_image = st.slider("Seconds per image", 1.0, 12.0, 4.0, 0.5)
    fps = st.selectbox("FPS", [24, 30, 60], index=1)
    include_voiceover = st.checkbox("Attach voiceover if available", value=True)
    fade_duration = st.slider("Fade in/out (seconds)", 0.0, 2.0, 0.3, 0.1)
    crossfade = st.checkbox("Crossfade between slides", value=False)
    zoom_effect = st.checkbox("Subtle zoom-in effect", value=False)

    st.markdown("### Background music (optional)")
    st.caption(
        "Upload a track or paste a royalty-free URL (e.g., Pixabay, Free Music Archive, or "
        "YouTube Audio Library). Always verify the license before publishing."
    )
    music_upload = st.file_uploader(
        "Upload background music",
        type=["mp3", "wav", "m4a", "aac", "ogg"],
        accept_multiple_files=False,
    )
    if music_upload is not None:
        st.session_state.background_music_bytes = music_upload.read()
        st.session_state.background_music_name = music_upload.name
        st.session_state.background_music_error = ""
    music_url = st.text_input(
        "Or paste a music URL",
        value=st.session_state.background_music_url,
        placeholder="https://example.com/track.mp3",
    )
    st.session_state.background_music_url = music_url
    if st.button("Fetch music from URL", use_container_width=True):
        music_bytes, error = _fetch_music_from_url(music_url.strip())
        if error:
            st.session_state.background_music_error = error
            st.session_state.background_music_bytes = None
            st.session_state.background_music_name = ""
        else:
            st.session_state.background_music_bytes = music_bytes
            st.session_state.background_music_name = music_url.split("/")[-1] or "background-music"
            st.session_state.background_music_error = ""
            st.toast("Background music loaded.")
    if st.session_state.background_music_error:
        st.warning(st.session_state.background_music_error)
    if st.session_state.background_music_bytes:
        st.success(f"Background music ready: {st.session_state.background_music_name or 'track'}")
        st.session_state.background_music_volume = st.slider(
            "Music volume",
            0.0,
            1.0,
            float(st.session_state.background_music_volume),
            0.05,
        )

    if st.button("Build video", type="primary", use_container_width=True):
        with st.spinner("Compiling video..."):
            voiceover_bytes = st.session_state.get("voiceover_bytes") if include_voiceover else None
            with tempfile.TemporaryDirectory() as temp_dir:
                image_paths = _write_scene_images(scenes, temp_dir)
                if not image_paths:
                    st.warning("No images found to compile. Generate images first.")
                    return

                clips = []
                for path in image_paths:
                    clip = ImageClip(path).set_duration(duration_per_image)
                    if zoom_effect:
                        clip = clip.fx(vfx.resize, lambda t: 1 + 0.03 * t / duration_per_image)
                    if fade_duration > 0:
                        clip = clip.fx(vfx.fadein, fade_duration).fx(vfx.fadeout, fade_duration)
                    clips.append(clip)

                if crossfade and len(clips) > 1:
                    video = concatenate_videoclips(clips, method="compose", padding=-fade_duration)
                else:
                    video = concatenate_videoclips(clips, method="compose")

                if voiceover_bytes:
                    audio_path = f"{temp_dir}/voiceover.mp3"
                    with open(audio_path, "wb") as f:
                        f.write(voiceover_bytes)
                    audio_clip = AudioFileClip(audio_path)
                    per_image = max(audio_clip.duration / len(clips), 0.5)
                    audio_clips = []
                    for path in image_paths:
                        clip = ImageClip(path).set_duration(per_image)
                        if zoom_effect:
                            clip = clip.fx(vfx.resize, lambda t: 1 + 0.03 * t / per_image)
                        if fade_duration > 0:
                            clip = clip.fx(vfx.fadein, fade_duration).fx(vfx.fadeout, fade_duration)
                        audio_clips.append(clip)
                    if crossfade and len(audio_clips) > 1:
                        video = concatenate_videoclips(
                            audio_clips,
                            method="compose",
                            padding=-fade_duration,
                        ).set_audio(audio_clip)
                    else:
                        video = concatenate_videoclips(audio_clips, method="compose").set_audio(audio_clip)

                background_music = st.session_state.get("background_music_bytes")
                if background_music:
                    name = st.session_state.get("background_music_name", "")
                    ext = ".mp3"
                    if "." in name:
                        ext = f".{name.split('.')[-1]}"
                    music_path = f"{temp_dir}/background_music{ext}"
                    with open(music_path, "wb") as f:
                        f.write(background_music)
                    music_clip = AudioFileClip(music_path)
                    video_duration = video.duration
                    if music_clip.duration < video_duration:
                        from moviepy.editor import concatenate_audioclips

                        loops = int(video_duration / music_clip.duration) + 1
                        music_clip = concatenate_audioclips([music_clip] * loops)
                    music_clip = music_clip.subclip(0, video_duration).volumex(
                        st.session_state.background_music_volume
                    )
                    if video.audio:
                        from moviepy.editor import CompositeAudioClip

                        video = video.set_audio(CompositeAudioClip([music_clip, video.audio]))
                    else:
                        video = video.set_audio(music_clip)

                output_path = f"{temp_dir}/history_forge_slideshow.mp4"
                video.write_videofile(
                    output_path,
                    fps=fps,
                    codec="libx264",
                    audio_codec="aac",
                    verbose=False,
                    logger=None,
                )
                with open(output_path, "rb") as f:
                    st.session_state.compiled_video_bytes = f.read()

        st.success("Video compiled.")

    if st.session_state.compiled_video_bytes:
        st.video(st.session_state.compiled_video_bytes)
        st.download_button(
            "Download MP4",
            data=st.session_state.compiled_video_bytes,
            file_name="history_forge_slideshow.mp4",
            mime="video/mp4",
            use_container_width=True,
        )



def _script_summary(script: str) -> str:
    text = (script or "").strip().replace("\n", " ")
    if not text:
        return "A quick history story."
    return text[:220].rsplit(" ", 1)[0] + "..."


def _title_options(topic: str, script: str) -> List[str]:
    base = (topic or "").strip()
    if not base:
        base = (script or "").strip().split(".")[0][:60] or "History Story"
    return [
        f"{base}: The Untold Story",
        f"{base} — Secrets Revealed",
        f"Inside {base}: What Really Happened",
    ]


def _description_for_script(script: str, topic: str) -> str:
    word_count = len((script or "").split())
    summary = _script_summary(script)
    if word_count <= 200:
        return f"{summary}\n\nSubscribe for more quick history stories."
    hashtags = "#history #documentary #ancienthistory #shorts #education"
    return (
        f"{summary}\n\n"
        "In this episode we explore the timeline, key figures, and hidden details that shaped the story. "
        "If you enjoy deep dives into the past, consider subscribing for weekly releases.\n\n"
        f"{hashtags}"
    )


def _thumbnail_prompt_variations(base_prompt: str) -> List[str]:
    base = base_prompt.strip()
    if not base:
        base = "High-contrast cinematic historical thumbnail. No text."
    return [
        f"{base} Dramatic lighting, strong subject contrast, crisp foreground.",
        f"{base} Wide establishing shot, epic scale, moody atmosphere.",
        f"{base} Tight portrait framing, intense emotion, cinematic shadows.",
    ]


def tab_titles_thumbnails() -> None:
    st.subheader("Titles, description & thumbnails")
    st.caption("Generate title ideas, a YouTube description, and thumbnail options from your script.")

    if not st.session_state.script_text.strip():
        st.warning("Paste or generate a script first.")
        return

    if st.button("Generate titles + description", type="primary", use_container_width=True):
        st.session_state.title_options = _title_options(
            st.session_state.active_story_title,
            st.session_state.script_text,
        )
        st.session_state.description_text = _description_for_script(
            st.session_state.script_text,
            st.session_state.active_story_title,
        )
        st.toast("Metadata ready.")

    if st.session_state.title_options:
        st.markdown("### Title ideas")
        for idx, title in enumerate(st.session_state.title_options, start=1):
            st.text_input(f"Title option {idx}", value=title, key=f"title_option_{idx}")

    if st.session_state.description_text:
        st.markdown("### Video description")
        description_value = st.text_area(
            "Description",
            value=st.session_state.description_text,
            height=160,
            key="video_description",
        )
        st.session_state.description_text = description_value

        c1, c2 = st.columns([1, 2])
        with c1:
            edit_mode = st.selectbox(
                "AI edit mode",
                ["refresh", "shorten", "expand", "add hashtags"],
                index=0,
            )
        with c2:
            if st.button("AI edit description", use_container_width=True):
                with st.spinner("Rewriting description..."):
                    rewritten = rewrite_description(
                        st.session_state.script_text,
                        st.session_state.description_text,
                        mode=edit_mode,
                    )
                st.session_state.description_text = rewritten
                st.session_state["video_description"] = rewritten
                st.toast("Description updated.")

    st.divider()
    st.markdown("### Thumbnail generation")
    st.selectbox(
        "Thumbnail aspect ratio",
        ["16:9", "9:16", "1:1"],
        index=["16:9", "9:16", "1:1"].index(st.session_state.thumbnail_aspect_ratio),
        key="thumbnail_aspect_ratio",
    )
    default_prompt = st.session_state.thumbnail_prompt or (
        f"High-contrast cinematic thumbnail for {st.session_state.active_story_title}. "
        "Bold historical imagery, dramatic lighting, clear focal subject. No text."
    )
    thumbnail_prompt = st.text_area(
        "Thumbnail prompt",
        value=default_prompt,
        height=90,
        key="thumbnail_prompt",
    )

    if st.button("Generate 3 prompt variations", use_container_width=True):
        st.session_state.thumbnail_prompt_variations = _thumbnail_prompt_variations(
            thumbnail_prompt
        )

    prompt_variations = st.session_state.thumbnail_prompt_variations
    if prompt_variations:
        st.markdown("#### Thumbnail prompt variations")
        for i, prompt in enumerate(prompt_variations, start=1):
            updated_prompt = st.text_area(
                f"Prompt {i}",
                value=prompt,
                height=80,
                key=f"thumb_prompt_{i}",
            )
            prompt_variations[i - 1] = updated_prompt
        st.session_state.thumbnail_prompt_variations = prompt_variations

    prompts = st.session_state.thumbnail_prompt_variations or [thumbnail_prompt]
    if len(st.session_state.thumbnail_images) != len(prompts):
        st.session_state.thumbnail_images = [None] * len(prompts)

    for i, prompt in enumerate(prompts, start=1):
        cols = st.columns([2, 1])
        with cols[0]:
            st.markdown(f"**Thumbnail prompt {i}**")
        with cols[1]:
            if st.button("Generate image", key=f"generate_thumbnail_{i}", use_container_width=True):
                scene = Scene(
                    index=i,
                    title=f"Thumbnail {i}",
                    script_excerpt=st.session_state.script_text[:240],
                    visual_intent="Create a compelling YouTube thumbnail.",
                    image_prompt=prompt,
                )
                updated = generate_image_for_scene(
                    scene,
                    aspect_ratio=st.session_state.thumbnail_aspect_ratio,
                    visual_style=st.session_state.visual_style,
                )
                if updated.image_bytes:
                    st.session_state.thumbnail_images[i - 1] = updated.image_bytes

        image_bytes = st.session_state.thumbnail_images[i - 1]
        if image_bytes:
            st.image(image_bytes, use_container_width=True)



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
            "Voiceover",
            "Compile Video",
            "Titles & Thumbnails",
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
        tab_voiceover()
    with tabs[6]:
        tab_compile_video()
    with tabs[7]:
        tab_titles_thumbnails()
    with tabs[8]:
        tab_export_package()


if __name__ == "__main__":
    main()
