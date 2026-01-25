import streamlit as st
from typing import List, Dict, Any
from datetime import datetime
import zipfile
import io
import json
import random

import requests
from supabase import create_client, Client

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


def _get_supabase_config() -> Dict[str, str]:
    return {
        "url": st.secrets.get("SUPABASE_URL", "").strip() or get_secret("SUPABASE_URL", "").strip(),
        "anon_key": st.secrets.get("SUPABASE_ANON_KEY", "").strip() or get_secret("SUPABASE_ANON_KEY", "").strip(),
        "email": st.secrets.get("SUPABASE_EMAIL", "").strip() or get_secret("SUPABASE_EMAIL", "").strip(),
        "password": st.secrets.get("SUPABASE_PASSWORD", "").strip() or get_secret("SUPABASE_PASSWORD", "").strip(),
        "bucket": st.secrets.get("SUPABASE_STORAGE_BUCKET", "").strip()
        or get_secret("SUPABASE_STORAGE_BUCKET", "").strip()
        or "scene-assets",
    }


def _init_supabase() -> Client | None:
    cfg = _get_supabase_config()
    if not cfg["url"] or not cfg["anon_key"]:
        return None
    if "supabase_client" not in st.session_state:
        client = create_client(cfg["url"], cfg["anon_key"])
        if cfg["email"] and cfg["password"]:
            try:
                auth = client.auth.sign_in_with_password(
                    {"email": cfg["email"], "password": cfg["password"]}
                )
                if auth and getattr(auth, "user", None):
                    st.session_state.supabase_owner_id = auth.user.id
            except Exception as exc:
                st.warning(
                    "Supabase sign-in failed. The app will run without a Supabase owner session."
                )
                st.session_state.supabase_auth_error = str(exc)
        st.session_state.supabase_client = client
    return st.session_state.supabase_client


def _get_owner_id(client: Client) -> str | None:
    owner_id = st.session_state.get("supabase_owner_id")
    if owner_id:
        return owner_id
    user = client.auth.get_user()
    if user and getattr(user, "user", None):
        owner_id = user.user.id
        st.session_state.supabase_owner_id = owner_id
        return owner_id
    return None


def _load_latest_story(client: Client) -> Dict[str, Any] | None:
    resp = client.table("stories").select("*").order("created_at", desc=True).limit(1).execute()
    if resp.data:
        return resp.data[0]
    return None


def _load_story_scenes(client: Client, story_id: str) -> List[Dict[str, Any]]:
    resp = (
        client.table("scenes")
        .select("*")
        .eq("story_id", story_id)
        .order("order_index")
        .execute()
    )
    return resp.data or []


def _load_scene_assets(client: Client, scene_ids: List[str]) -> List[Dict[str, Any]]:
    if not scene_ids:
        return []
    resp = client.table("assets").select("*").in_("scene_id", scene_ids).execute()
    return resp.data or []


def _fetch_signed_asset_bytes(client: Client, bucket: str, path: str) -> bytes | None:
    try:
        signed = client.storage.from_(bucket).create_signed_url(path, 3600)
        signed_url = signed.get("signedURL") if isinstance(signed, dict) else None
        if signed_url:
            resp = requests.get(signed_url, timeout=30)
            if resp.ok:
                return resp.content
    except Exception:
        return None
    return None


def _upload_asset_bytes(client: Client, bucket: str, path: str, data: bytes) -> bool:
    try:
        client.storage.from_(bucket).upload(
            path,
            data,
            {"content-type": "image/png", "upsert": True},
        )
        return True
    except Exception:
        return False


def _sync_scene_order(scenes: List[Scene], client: Client | None = None) -> None:
    for idx, scene in enumerate(scenes, start=1):
        scene.index = idx
        if client and scene.supabase_id:
            client.table("scenes").update({"order_index": idx}).eq("id", scene.supabase_id).execute()


def _pack_scene_text(scene: Scene) -> str:
    payload = {
        "title": scene.title,
        "script_excerpt": scene.script_excerpt,
        "visual_intent": scene.visual_intent,
        "image_prompt": scene.image_prompt,
    }
    return json.dumps(payload, ensure_ascii=False)


def _unpack_scene_text(raw_text: str) -> Dict[str, str]:
    if not raw_text:
        return {"title": "", "script_excerpt": "", "visual_intent": "", "image_prompt": ""}
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            return {
                "title": str(data.get("title", "")),
                "script_excerpt": str(data.get("script_excerpt", "")),
                "visual_intent": str(data.get("visual_intent", "")),
                "image_prompt": str(data.get("image_prompt", "")),
            }
    except Exception:
        pass
    return {
        "title": "",
        "script_excerpt": raw_text,
        "visual_intent": "",
        "image_prompt": "",
    }

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
    supabase = _init_supabase()
    supabase_cfg = _get_supabase_config()

    st.title("🔥 The History Forge")
    st.caption("Generate a YouTube history script + scenes + prompts + images.")

    if supabase and "supabase_loaded" not in st.session_state:
        story = _load_latest_story(supabase)
        if story:
            settings = story.get("settings") or {}
            st.session_state.story_id = story["id"]
            st.session_state.script = settings.get("script", "")
            st.session_state.topic = settings.get("topic", "")
            st.session_state.voice_id = settings.get("voice_id", "")
            st.session_state.story_settings = settings
            scene_rows = _load_story_scenes(supabase, story["id"])
            scene_ids = [row["id"] for row in scene_rows]
            assets = _load_scene_assets(supabase, scene_ids)
            assets_by_scene: Dict[str, List[Dict[str, Any]]] = {}
            for asset in assets:
                assets_by_scene.setdefault(asset["scene_id"], []).append(asset)

            scenes: List[Scene] = []
            for row in scene_rows:
                parsed = _unpack_scene_text(row.get("script_text") or "")
                scene_assets = assets_by_scene.get(row["id"], [])
                image_variations: List[bytes | None] = []
                primary_index = 0
                for asset in scene_assets:
                    if asset.get("type") != "image":
                        continue
                    meta = asset.get("generation_meta") or {}
                    variation_index = int(meta.get("variation_index", len(image_variations)))
                    path = asset.get("url") or ""
                    img_bytes = None
                    if path:
                        img_bytes = _fetch_signed_asset_bytes(supabase, supabase_cfg["bucket"], path)
                    while len(image_variations) <= variation_index:
                        image_variations.append(None)
                    image_variations[variation_index] = img_bytes
                    if meta.get("is_primary"):
                        primary_index = variation_index

                scenes.append(
                    Scene(
                        index=int(row.get("order_index", len(scenes) + 1)),
                        title=parsed["title"] or f"Scene {len(scenes) + 1}",
                        script_excerpt=parsed["script_excerpt"],
                        visual_intent=parsed["visual_intent"],
                        image_prompt=parsed["image_prompt"],
                        image_bytes=image_variations[primary_index] if image_variations else None,
                        image_variations=image_variations,
                        primary_image_index=primary_index,
                        supabase_id=row.get("id"),
                        status=row.get("status") or "active",
                    )
                )
            st.session_state.scenes = scenes
        st.session_state.supabase_loaded = True

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
            status.update(label="1/5 Writing script…")
            script = generate_script(topic=topic, length=length, tone=tone)
            st.session_state.script = script
            st.session_state.voice_id = voice_id

            status.update(label=f"2/5 Splitting into {num_images} scenes…")
            scenes = split_script_into_scenes(script, max_scenes=num_images)
            st.session_state.scenes = scenes

            status.update(label="3/5 Writing prompts…")
            scenes = generate_prompts_for_scenes(scenes, tone=tone, style=visual_style)
            st.session_state.scenes = scenes

            status.update(label="4/5 Generating images…")
            scenes_out = []
            failed_idxs = []
            
            # Pass 1
            for s in scenes:
                variations = []
                for _ in range(variations_per_scene):
                    s2 = generate_image_for_scene(
                        s,
                        aspect_ratio=aspect_ratio,
                        visual_style=visual_style,
                    )
                    variations.append(s2.image_bytes)
                s.image_variations = variations
                s.primary_image_index = 0
                s.image_bytes = variations[0] if variations else None
                if any(img is None for img in variations):
                    failed_idxs.append(s.index)
                scenes_out.append(s)
            
            # Pass 2 (retry failures once)
            if failed_idxs:
                status.update(label=f"4/5 Retrying {len(failed_idxs)} failed images…")
                for i in range(len(scenes_out)):
                    if scenes_out[i].index in failed_idxs:
                        updated_variations = []
                        for img in scenes_out[i].image_variations:
                            if img:
                                updated_variations.append(img)
                                continue
                            s2 = generate_image_for_scene(
                                scenes_out[i],
                                aspect_ratio=aspect_ratio,
                                visual_style=visual_style,
                            )
                            updated_variations.append(s2.image_bytes)
                        scenes_out[i].image_variations = updated_variations
                        primary = _get_primary_image(scenes_out[i])
                        scenes_out[i].image_bytes = primary
            
            # Count remaining failures
            failures = sum(
                1 for s in scenes_out if any(img is None for img in s.image_variations)
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

        if supabase:
            owner_id = _get_owner_id(supabase)
            if owner_id:
                settings = {
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
                story_resp = (
                    supabase.table("stories")
                    .insert(
                        {
                            "owner_id": owner_id,
                            "title": topic or "Untitled Story",
                            "settings": settings,
                        }
                    )
                    .execute()
                )
                story_id = story_resp.data[0]["id"] if story_resp.data else None
                if story_id:
                    st.session_state.story_id = story_id
                    st.session_state.story_settings = settings
                    scenes_insert = []
                    for s in st.session_state.scenes:
                        scenes_insert.append(
                            {
                                "story_id": story_id,
                                "owner_id": owner_id,
                                "order_index": s.index,
                                "script_text": _pack_scene_text(s),
                                "status": s.status,
                            }
                        )
                    scenes_resp = supabase.table("scenes").insert(scenes_insert).execute()
                    if scenes_resp.data:
                        for s, row in zip(st.session_state.scenes, scenes_resp.data):
                            s.supabase_id = row.get("id")
                        st.session_state.scenes = st.session_state.scenes

                    for s in st.session_state.scenes:
                        if not s.supabase_id:
                            continue
                        if not s.image_variations:
                            continue
                        for idx, img in enumerate(s.image_variations):
                            if not img:
                                continue
                            path = f"{story_id}/{s.supabase_id}/image_{idx + 1:02d}.png"
                            uploaded = _upload_asset_bytes(supabase, supabase_cfg["bucket"], path, img)
                            if uploaded:
                                supabase.table("assets").insert(
                                    {
                                        "scene_id": s.supabase_id,
                                        "owner_id": owner_id,
                                        "type": "image",
                                        "url": path,
                                        "generation_meta": {
                                            "variation_index": idx,
                                            "is_primary": idx == s.primary_image_index,
                                        },
                                    }
                                ).execute()


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
                if supabase and st.session_state.get("story_id"):
                    story_id = st.session_state.story_id
                    settings = st.session_state.get("story_settings", {})
                    settings["script"] = st.session_state.script
                    supabase.table("stories").update({"settings": settings}).eq("id", story_id).execute()
                    st.session_state.story_settings = settings
                st.success("Saved.")

    with tab_visuals:
        st.subheader("Scenes & Visuals")
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
                            _sync_scene_order(scenes, supabase)
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
                            _sync_scene_order(scenes, supabase)
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
                            if supabase and s.supabase_id:
                                supabase.table("scenes").update({"status": "deleted"}).eq("id", s.supabase_id).execute()
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
                            if supabase and s.supabase_id:
                                supabase.table("scenes").update({"status": "active"}).eq("id", s.supabase_id).execute()
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
                        st.error("Image missing for this scene. Check logs for '[Gemini image gen failed]'.")

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
                                    if supabase and s.supabase_id:
                                        assets = (
                                            supabase.table("assets")
                                            .select("*")
                                            .eq("scene_id", s.supabase_id)
                                            .eq("type", "image")
                                            .execute()
                                        ).data or []
                                        for asset in assets:
                                            meta = asset.get("generation_meta") or {}
                                            variation_index = int(meta.get("variation_index", 0))
                                            meta["is_primary"] = variation_index == idx
                                            supabase.table("assets").update({"generation_meta": meta}).eq("id", asset["id"]).execute()
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
                                if supabase and s.supabase_id:
                                    supabase.table("scenes").update(
                                        {"script_text": _pack_scene_text(s)}
                                    ).eq("id", s.supabase_id).execute()
                                st.success("Prompt updated. Now regenerate the image.")
                                st.rerun()

                    with c2:
                        if st.button("🔄 Regenerate primary image", key=f"regen_{s.index}", use_container_width=True):
                            try:
                                if supabase and s.supabase_id:
                                    owner_id = _get_owner_id(supabase)
                                    if owner_id:
                                        supabase.table("jobs").insert(
                                            {
                                                "scene_id": s.supabase_id,
                                                "owner_id": owner_id,
                                                "kind": "image_regen",
                                                "status": "queued",
                                                "progress": 0,
                                            }
                                        ).execute()
                                updated = generate_image_for_scene(
                                    s,
                                    aspect_ratio=aspect_ratio,
                                    visual_style=visual_style,
                                )
                                if s.image_variations:
                                    s.image_variations[s.primary_image_index] = updated.image_bytes
                                s.image_bytes = updated.image_bytes
                                if supabase and s.supabase_id and updated.image_bytes:
                                    owner_id = _get_owner_id(supabase)
                                    if owner_id:
                                        path = f"{st.session_state.story_id}/{s.supabase_id}/image_{s.primary_image_index + 1:02d}.png"
                                        uploaded = _upload_asset_bytes(
                                            supabase, supabase_cfg["bucket"], path, updated.image_bytes
                                        )
                                        if uploaded:
                                            supabase.table("assets").insert(
                                                {
                                                    "scene_id": s.supabase_id,
                                                    "owner_id": owner_id,
                                                    "type": "image",
                                                    "url": path,
                                                    "generation_meta": {
                                                        "variation_index": s.primary_image_index,
                                                        "is_primary": True,
                                                    },
                                                }
                                            ).execute()
                                        supabase.table("jobs").update(
                                            {"status": "complete", "progress": 100}
                                        ).eq("scene_id", s.supabase_id).eq("kind", "image_regen").execute()
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
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_EMAIL`
- `SUPABASE_PASSWORD`
- optional: `SUPABASE_STORAGE_BUCKET` (default: `scene-assets`)

If images fail, check logs for:
- `[Gemini image gen failed]`
- `[Gemini image gen final] FAILED`
""".strip()
        )

if __name__ == "__main__":
    main()
