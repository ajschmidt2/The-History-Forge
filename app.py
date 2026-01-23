import streamlit as st
from typing import Any, Dict, List, Optional
from datetime import datetime

# ---- Imports from your utils.py ----
# These must exist in your repo.
import streamlit as st

try:
    import utils
except Exception as e:
    st.error("Failed to import utils.py. Open 'Manage app' → logs for the full error.")
    st.exception(e)
    st.stop()

# Resolve required functions (supports older/newer naming)
generate_script = getattr(utils, "generate_script", None)
split_script_into_scenes = getattr(utils, "split_script_into_scenes", None)
generate_prompts = getattr(utils, "generate_prompts", None)

# images function name might differ depending on which patch you applied
generate_images_for_scenes = (
    getattr(utils, "generate_images_for_scenes", None)
    or getattr(utils, "generate_images", None)
)

missing = [
    name for name, fn in {
        "generate_script": generate_script,
        "split_script_into_scenes": split_script_into_scenes,
        "generate_prompts": generate_prompts,
        "generate_images_for_scenes (or generate_images)": generate_images_for_scenes,
    }.items()
    if fn is None
]

if missing:
    st.error("Your utils.py is missing required functions:")
    for m in missing:
        st.write(f"- {m}")
    st.info("Fix utils.py to include these functions, then redeploy.")
    st.stop()


# ----------------------------
# Security (simple password gate)
# ----------------------------
def _require_login() -> None:
    """
    Optional: If you set `app_password` in Streamlit secrets, the app will require it.
    If not set, app runs without login.
    """
    pw = st.secrets.get("app_password", "").strip() if hasattr(st, "secrets") else ""
    if not pw:
        return  # No password configured → no login required

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return

    st.title("🔒 The History Forge")
    st.caption("Enter password to continue.")

    entered = st.text_input("Password", type="password")
    if st.button("Log in", use_container_width=True):
        if entered == pw:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")

    st.stop()


# ----------------------------
# Helpers
# ----------------------------
def _as_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x)


def _get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    """Supports dicts, pydantic models, dataclasses, plain objects."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    # pydantic or object attribute
    return getattr(obj, name, default)


def _normalize_scene(raw: Any, idx: int, script: str = "") -> Dict[str, Any]:
    """
    Coerce whatever split_script_into_scenes() returned into a stable dict schema:

    {
      "index": int,
      "title": str,
      "script_excerpt": str,
      "visual_intent": str,
      "prompt": str,
      "image": PIL.Image | None
    }
    """
    # Candidates for fields
    title = _get_attr_or_key(raw, "title", None)
    excerpt = _get_attr_or_key(raw, "script_excerpt", None)
    visual_intent = _get_attr_or_key(raw, "visual_intent", None)

    # Some splitters name these differently
    excerpt = excerpt or _get_attr_or_key(raw, "excerpt", None) or _get_attr_or_key(raw, "narration", None) or _get_attr_or_key(raw, "text", None)
    prompt = _get_attr_or_key(raw, "prompt", None) or _get_attr_or_key(raw, "image_prompt", None)

    # If scene is just a string
    if isinstance(raw, str):
        excerpt = excerpt or raw

    # Fallback title
    if not title:
        # Prefer a short title derived from excerpt, else generic
        src = (excerpt or prompt or "").strip()
        title = (src[:60] + "...") if len(src) > 60 else (src if src else "Visual Scene")

    # Fallback excerpt: take a slice of the script if we have it
    if not excerpt and script:
        # naive chunking fallback
        approx_len = max(160, min(320, len(script) // max(1, 6)))
        start = min(len(script), (idx - 1) * approx_len)
        excerpt = script[start : start + approx_len].strip()

    # Fallback visual intent: derive from prompt or title
    if not visual_intent:
        if prompt:
            visual_intent = f"Illustrate: {prompt[:180]}{'...' if len(prompt) > 180 else ''}"
        else:
            visual_intent = f"Visualize scene {idx}: {title}"

    return {
        "index": idx,
        "title": title,
        "script_excerpt": excerpt or "",
        "visual_intent": visual_intent or "",
        "prompt": prompt or "",
        "image": None,
        "raw": raw,  # keep original for debugging if needed
    }


def _normalize_scenes(raw_scenes: Any, script: str = "") -> List[Dict[str, Any]]:
    """
    Accepts list/dict/string and returns list[scene_dict].
    """
    if raw_scenes is None:
        return []
    if isinstance(raw_scenes, dict):
        # Sometimes models return {"scenes":[...]}
        if "scenes" in raw_scenes and isinstance(raw_scenes["scenes"], list):
            raw_scenes = raw_scenes["scenes"]
        else:
            raw_scenes = [raw_scenes]
    if isinstance(raw_scenes, str):
        raw_scenes = [raw_scenes]
    if not isinstance(raw_scenes, list):
        raw_scenes = [raw_scenes]

    out: List[Dict[str, Any]] = []
    for i, sc in enumerate(raw_scenes, start=1):
        out.append(_normalize_scene(sc, i, script=script))
    return out


def _export_package(script: str, scenes: List[Dict[str, Any]]) -> bytes:
    """
    Create a zip-like export (simple) using a single text bundle.
    If you already have a ZIP exporter elsewhere, keep it.
    """
    import io, json, zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("script.txt", script or "")
        z.writestr("scenes.json", json.dumps(scenes, indent=2, default=str))

        # Add images
        for sc in scenes:
            img = sc.get("image")
            if img is None:
                continue
            img_buf = io.BytesIO()
            try:
                img.save(img_buf, format="PNG")
                z.writestr(f"images/scene_{sc['index']:02d}.png", img_buf.getvalue())
            except Exception:
                # Skip image if it can't be saved
                pass

    return buf.getvalue()


# ----------------------------
# UI sections
# ----------------------------
def _sidebar_config() -> Dict[str, Any]:
    st.sidebar.header("⚙️ Controls")

    topic = st.sidebar.text_input("Topic", value=st.session_state.get("topic", "The mystery of Alexander the Great's tomb"))
    length = st.sidebar.selectbox(
        "Length",
        ["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"],
        index=1,
    )
    tone = st.sidebar.selectbox(
        "Tone",
        ["Cinematic", "Mysterious", "Educational", "Eerie"],
        index=0,
    )
    aspect_ratio = st.sidebar.selectbox("Image aspect ratio", ["16:9", "9:16", "1:1"], index=0)
    images_per_scene = st.sidebar.slider("Images per scene", 1, 3, 1)

    st.sidebar.divider()
    generate = st.sidebar.button("✨ Generate Package", type="primary", use_container_width=True)

    return {
        "topic": topic,
        "length": length,
        "tone": tone,
        "aspect_ratio": aspect_ratio,
        "images_per_scene": images_per_scene,
        "generate": generate,
    }


def _generate_all(cfg: Dict[str, Any]) -> None:
    st.session_state.topic = cfg["topic"]

    with st.status("Generating…", expanded=True) as status:
        status.update(label="1/4 Writing narration script…")
        script = generate_script(cfg["topic"], cfg["length"], cfg["tone"])
        st.session_state.script = script

        status.update(label="2/4 Splitting into scenes…")
        raw_scenes = split_script_into_scenes(script)
        scenes = _normalize_scenes(raw_scenes, script=script)

        status.update(label="3/4 Creating visual prompts…")
        # generate_prompts may return list of prompts aligned to scenes, or may mutate scenes.
        prompts = generate_prompts(scenes, cfg["tone"])

        # Apply prompts to scenes if returned separately
        if isinstance(prompts, list):
            for i, p in enumerate(prompts):
                if i < len(scenes):
                    scenes[i]["prompt"] = _as_str(p)

        status.update(label="4/4 Generating images… (this can take a bit)")
        # images_per_scene: if >1, we’ll just generate once per scene for now
        # (easy future upgrade: store list of images per scene).
        imgs = generate_images_for_scenes(
            scenes,
            aspect_ratio=cfg["aspect_ratio"],
        )

        # Attach images back
        if isinstance(imgs, list):
            for i, im in enumerate(imgs):
                if i < len(scenes):
                    scenes[i]["image"] = im

        st.session_state.scenes = scenes
        status.update(label="Done ✅", state="complete")


def _script_tab() -> None:
    st.subheader("Narration Script")

    script = st.session_state.get("script", "")
    if not script:
        st.info("Generate a package to see the script here.")
        return

    st.text_area("Script (editable)", value=script, height=420, key="script_editor")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("💾 Save edits to script", use_container_width=True):
            st.session_state.script = st.session_state.script_editor
            st.success("Saved.")
    with col2:
        zip_bytes = _export_package(st.session_state.get("script", ""), st.session_state.get("scenes", []))
        st.download_button(
            "⬇️ Export Package (ZIP)",
            data=zip_bytes,
            file_name=f"history_forge_{datetime.now().strftime('%Y%m%d_%H%M')}.zip",
            use_container_width=True,
        )


def _visuals_tab(cfg: Dict[str, Any]) -> None:
    st.subheader("Scenes & Visuals")

    scenes: List[Dict[str, Any]] = st.session_state.get("scenes", [])
    if not scenes:
        st.info("Generate a package to see scenes and images here.")
        return

    for sc in scenes:
        idx = sc.get("index", 0)
        title = sc.get("title") or f"Scene {idx}"
        with st.expander(f"Scene {idx}: {title}", expanded=(idx == 1)):
            st.markdown("**Scene excerpt**")
            excerpt = sc.get("script_excerpt", "")
            if excerpt:
                st.write(excerpt)
            else:
                st.caption("No script excerpt available for this scene.")

            st.markdown("**Visual intent**")
            vi = sc.get("visual_intent", "")
            if vi:
                st.write(vi)
            else:
                st.caption("No visual intent available for this scene.")

            st.markdown("**Image prompt**")
            prompt = sc.get("prompt", "")
            if prompt:
                st.code(prompt, language="text")
            else:
                st.caption("No prompt available for this scene (yet).")

            # Image display (safe)
            img = sc.get("image")
            if img is not None:
                st.image(img, caption=f"Scene {idx} image", use_container_width=True)
            else:
                st.warning("No image generated for this scene (yet).")

            # Per-scene regeneration
            colA, colB = st.columns([1, 2])
            with colA:
                if st.button(f"🔄 Regenerate image (Scene {idx})", key=f"regen_{idx}", use_container_width=True):
                    # regenerate just this image
                    one = generate_images_for_scenes([sc], aspect_ratio=cfg["aspect_ratio"])
                    if isinstance(one, list) and one and one[0] is not None:
                        sc["image"] = one[0]
                        st.success("Image regenerated.")
                        st.rerun()
                    else:
                        st.error("Image regeneration failed. Check logs / rate limits.")
            with colB:
                refine = st.text_input(
                    f"Refine prompt (Scene {idx})",
                    value="",
                    key=f"refine_{idx}",
                    placeholder="e.g., darker mood, closer shot, more cinematic lighting…",
                )
                if st.button(f"✏️ Apply refinement to prompt (Scene {idx})", key=f"apply_refine_{idx}", use_container_width=True):
                    if refine.strip():
                        sc["prompt"] = (prompt + "\n\nRefinement: " + refine.strip()).strip()
                        st.success("Prompt updated.")
                        st.rerun()


def main() -> None:
    st.set_page_config(page_title="The History Forge", layout="wide")
    _require_login()

    st.title("🔥 The History Forge")
    st.caption("Generate a complete history YouTube package: script + scenes + visuals + images.")

    cfg = _sidebar_config()

    if cfg["generate"]:
        _generate_all(cfg)

    tab1, tab2 = st.tabs(["📝 Script", "🖼️ Scenes & Visuals"])
    with tab1:
        _script_tab()
    with tab2:
        _visuals_tab(cfg)

    # Footer helpers
    with st.sidebar.expander("ℹ️ Help", expanded=False):
        st.markdown(
            """
- Add your API keys in Streamlit Cloud → **Secrets**:
  - `openai_api_key`
  - `gemini_api_key`
  - optional: `app_password`
- If images fail, it’s usually rate limits or model availability.
            """.strip()
        )

    if st.sidebar.button("Log out", use_container_width=True):
        if "authenticated" in st.session_state:
            st.session_state.authenticated = False
        st.rerun()


if __name__ == "__main__":
    main()
