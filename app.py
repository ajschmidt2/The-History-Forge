# app.py
import streamlit as st
from typing import Any, Dict, List, Optional
from datetime import datetime

import utils  # import the whole module so the app still loads even if function names change slightly


# ----------------------------
# Simple password gate (optional)
# ----------------------------
def require_login() -> None:
    """
    If you set `app_password` in Streamlit secrets, users must enter it to use the app.
    If not set, the app runs without a login gate.
    """
    pw = st.secrets.get("app_password", "").strip() if hasattr(st, "secrets") else ""
    if not pw:
        return

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return

    st.title("🔒 The History Forge")
    st.caption("Enter password to continue")

    entered = st.text_input("Password", type="password")
    if st.button("Log in", use_container_width=True):
        if entered == pw:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")

    st.stop()


# ----------------------------
# Scene normalization helpers
# ----------------------------
def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def normalize_scenes(raw_scenes: Any, script: str = "") -> List[Dict[str, Any]]:
    """
    Forces a stable schema for every scene so UI never crashes:

    scene = {
      "index": int,
      "title": str,
      "text": str,              # excerpt / narration snippet
      "visual_intent": str,
      "prompt": str,
      "image": PIL.Image | None
    }
    """
    if raw_scenes is None:
        return []

    # common shapes: {"scenes":[...]}, list, str, dict, pydantic model(s)
    if isinstance(raw_scenes, dict) and "scenes" in raw_scenes and isinstance(raw_scenes["scenes"], list):
        raw_scenes = raw_scenes["scenes"]
    elif isinstance(raw_scenes, (str, dict)):
        raw_scenes = [raw_scenes]

    if not isinstance(raw_scenes, list):
        raw_scenes = [raw_scenes]

    out: List[Dict[str, Any]] = []
    for i, sc in enumerate(raw_scenes, start=1):
        title = _get(sc, "title", "") or ""
        text = _get(sc, "text", "") or _get(sc, "script_excerpt", "") or _get(sc, "excerpt", "") or _get(sc, "narration", "") or ""
        visual_intent = _get(sc, "visual_intent", "") or ""

        prompt = _get(sc, "prompt", "") or _get(sc, "image_prompt", "") or ""
        img = _get(sc, "image", None)

        # fallback title
        if not title:
            seed = (text or prompt or "").strip()
            title = (seed[:60] + "...") if len(seed) > 60 else (seed if seed else "Visual Scene")

        # fallback text (derive chunk from script if needed)
        if not text and script:
            approx = max(180, min(360, len(script) // max(1, 6)))
            start = min(len(script), (i - 1) * approx)
            text = script[start : start + approx].strip()

        # fallback visual intent
        if not visual_intent:
            if text:
                visual_intent = f"Create a cinematic historical visual matching this excerpt: {text[:180]}..."
            else:
                visual_intent = f"Visualize: {title}"

        out.append(
            {
                "index": i,
                "title": title,
                "text": text,
                "visual_intent": visual_intent,
                "prompt": prompt,
                "image": img,
            }
        )
    return out


# ----------------------------
# Export helper
# ----------------------------
def export_zip(script: str, scenes: List[Dict[str, Any]]) -> bytes:
    import io, json, zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("script.txt", script or "")
        z.writestr("scenes.json", json.dumps(scenes, indent=2, default=str))

        for sc in scenes:
            img = sc.get("image")
            if img is None:
                continue
            try:
                img_buf = io.BytesIO()
                img.save(img_buf, format="PNG")
                z.writestr(f"images/scene_{sc['index']:02d}.png", img_buf.getvalue())
            except Exception:
                pass

    return buf.getvalue()


# ----------------------------
# UI
# ----------------------------
def sidebar_config() -> Dict[str, Any]:
    st.sidebar.header("⚙️ Controls")

    topic = st.sidebar.text_input(
        "Topic",
        value=st.session_state.get("topic", "The mystery of Alexander the Great's tomb"),
    )

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

    st.sidebar.divider()
    generate = st.sidebar.button("✨ Generate Package", type="primary", use_container_width=True)

    st.sidebar.divider()
    debug_mode = st.sidebar.toggle("Debug mode (show API errors)", value=True)

    return {
        "topic": topic,
        "length": length,
        "tone": tone,
        "aspect_ratio": aspect_ratio,
        "generate": generate,
        "debug_mode": debug_mode,
    }


def generate_all(cfg: Dict[str, Any]) -> None:
    st.session_state.topic = cfg["topic"]

    with st.status("Generating…", expanded=True) as status:
        status.update(label="1/4 Writing script…")
        script = utils.generate_script(cfg["topic"], cfg["length"], cfg["tone"])
        st.session_state.script = script

        status.update(label="2/4 Splitting into scenes…")
        raw_scenes = utils.split_script_into_scenes(script)
        scenes = normalize_scenes(raw_scenes, script=script)

        status.update(label="3/4 Writing image prompts…")
        prompts = utils.generate_prompts(scenes, cfg["tone"])
        if isinstance(prompts, list):
            for i, p in enumerate(prompts):
                if i < len(scenes):
                    scenes[i]["prompt"] = str(p).strip()

        status.update(label="4/4 Generating images…")
        imgs = utils.generate_images_for_scenes(scenes, aspect_ratio=cfg["aspect_ratio"])
        if isinstance(imgs, list):
            for i, im in enumerate(imgs):
                if i < len(scenes):
                    scenes[i]["image"] = im

        st.session_state.scenes = scenes
        status.update(label="Done ✅", state="complete")


def script_tab() -> None:
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
        zip_bytes = export_zip(st.session_state.get("script", ""), st.session_state.get("scenes", []))
        st.download_button(
            "⬇️ Export Package (ZIP)",
            data=zip_bytes,
            file_name=f"history_forge_{datetime.now().strftime('%Y%m%d_%H%M')}.zip",
            use_container_width=True,
        )


def visuals_tab(cfg: Dict[str, Any]) -> None:
    st.subheader("Scenes & Visuals")
    scenes: List[Dict[str, Any]] = st.session_state.get("scenes", [])
    if not scenes:
        st.info("Generate a package to see scenes and images here.")
        return

    for sc in scenes:
        idx = sc.get("index", 0)
        title = sc.get("title", f"Scene {idx}")

        with st.expander(f"Scene {idx}: {title}", expanded=(idx == 1)):
            st.markdown("**Scene excerpt**")
            if sc.get("text"):
                st.write(sc["text"])
            else:
                st.caption("No excerpt available for this scene.")

            st.markdown("**Visual intent**")
            if sc.get("visual_intent"):
                st.write(sc["visual_intent"])
            else:
                st.caption("No visual intent available for this scene.")

            st.markdown("**Image prompt**")
            if sc.get("prompt"):
                st.code(sc["prompt"], language="text")
            else:
                st.caption("No prompt available for this scene (yet).")

            img = sc.get("image")
            if img is not None:
                st.image(img, caption=f"Scene {idx} image", use_container_width=True)
            else:
                st.warning("No image generated for this scene (yet).")

            colA, colB = st.columns([1, 2])

            with colB:
                refine = st.text_input(
                    f"Refine prompt (Scene {idx})",
                    value="",
                    key=f"refine_{idx}",
                    placeholder="e.g., closer shot, warmer lighting, fog, cinematic shadows…",
                )
                if st.button(f"✏️ Apply refinement (Scene {idx})", key=f"apply_ref_{idx}", use_container_width=True):
                    if refine.strip():
                        base = sc.get("prompt", "").strip() or sc.get("visual_intent", "").strip() or sc.get("text", "").strip()
                        sc["prompt"] = (base + "\n\nRefinement: " + refine.strip()).strip()
                        st.success("Prompt updated.")
                        st.rerun()

            with colA:
                if st.button(f"🔄 Regenerate image (Scene {idx})", key=f"regen_{idx}", use_container_width=True):
                    # prevent accidental double-click hammering
                    import time
                    now = time.time()
                    last = st.session_state.get("last_regen_time", 0.0)
                    if now - last < 2.0:
                        st.warning("Please wait a moment between regenerations.")
                        st.stop()
                    st.session_state["last_regen_time"] = now

                    try:
                        one = utils.generate_images_for_scenes([sc], aspect_ratio=cfg["aspect_ratio"])

                        if cfg["debug_mode"]:
                            st.write("Debug: returned type =", type(one).__name__)
                            st.write("Debug: returned len =", (len(one) if isinstance(one, list) else "n/a"))
                            st.write("Debug: first item type =", (type(one[0]).__name__ if isinstance(one, list) and one else "n/a"))

                        if isinstance(one, list) and one and one[0] is not None:
                            sc["image"] = one[0]
                            st.success("Image regenerated.")
                            st.rerun()
                        else:
                            st.error("Gemini returned no image bytes. Open Manage app → Logs for the exact error details.")

                    except Exception as e:
                        st.error("Image regeneration threw an exception:")
                        st.exception(e)


def main() -> None:
    st.set_page_config(page_title="The History Forge", layout="wide")
    require_login()

    st.title("🔥 The History Forge")
    st.caption("Generate a complete history YouTube package: script + scenes + visual prompts + images.")

    cfg = sidebar_config()

    if cfg["generate"]:
        generate_all(cfg)

    tab1, tab2 = st.tabs(["📝 Script", "🖼️ Scenes & Visuals"])
    with tab1:
        script_tab()
    with tab2:
        visuals_tab(cfg)

    with st.sidebar.expander("ℹ️ Setup checklist", expanded=False):
        st.markdown(
            """
**Streamlit Cloud → Secrets**
- `openai_api_key` (script + prompts)
- `gemini_api_key` (images)
- optional: `app_password` (login gate)

If images still fail, check **Manage app → Logs** for the `[Gemini …]` debug lines.
            """.strip()
        )

    if st.sidebar.button("Log out", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()


if __name__ == "__main__":
    main()
