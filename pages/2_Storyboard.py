import io
from typing import Any, Iterable

import streamlit as st
from PIL import Image, ImageDraw, ImageFont

from app import require_login, _get_primary_image, _sync_scene_order
from utils import Scene, generate_image_for_scene


DASHBOARD_CSS = """
<style>
:root {
  --bg-light: #F5F5F7;
  --bg-dark: #0A0A0A;
  --panel-light: rgba(255,255,255,0.7);
  --panel-dark: rgba(30,30,30,0.7);
  --border-light: rgba(0,0,0,0.08);
  --border-dark: rgba(255,255,255,0.10);
  --text-muted-light: rgba(15,23,42,0.65);
  --text-muted-dark: rgba(226,232,240,0.70);
  --radius: 16px;
}

.stApp {
  background: var(--bg-light);
}
@media (prefers-color-scheme: dark) {
  .stApp { background: var(--bg-dark); }
}

section[data-testid="stSidebar"] > div {
  background: rgba(255,255,255,0.50);
  backdrop-filter: blur(12px);
  border-right: 1px solid var(--border-light);
}
@media (prefers-color-scheme: dark) {
  section[data-testid="stSidebar"] > div {
    background: rgba(0,0,0,0.50);
    border-right: 1px solid var(--border-dark);
  }
}

.hf-card {
  background: rgba(255,255,255,0.90);
  border: 1px solid var(--border-light);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: 0 6px 20px rgba(0,0,0,0.06);
}
@media (prefers-color-scheme: dark) {
  .hf-card {
    background: rgba(15,23,42,0.55);
    border: 1px solid var(--border-dark);
  }
}

.hf-card-body {
  padding: 14px 14px 16px 14px;
}

.hf-chip {
  display: inline-block;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(0,0,0,0.55);
  color: white;
}

.hf-muted {
  color: var(--text-muted-light);
}
@media (prefers-color-scheme: dark) {
  .hf-muted { color: var(--text-muted-dark); }
}

.hf-header {
  padding: 10px 6px 2px 6px;
}
.hf-title {
  font-size: 28px;
  font-weight: 800;
  margin: 0;
}
.hf-subtitle {
  margin-top: 4px;
  font-size: 14px;
}
.hf-toolbar {
  display:flex;
  gap: 10px;
  align-items:center;
  justify-content: space-between;
  padding: 10px 0 4px 0;
}
.hf-tabs {
  display:flex;
  gap: 6px;
  padding: 6px;
  border-radius: 14px;
  background: rgba(15,23,42,0.06);
}
@media (prefers-color-scheme: dark) {
  .hf-tabs { background: rgba(148,163,184,0.10); }
}
.hf-tab {
  font-size: 12px;
  font-weight: 700;
  padding: 6px 10px;
  border-radius: 12px;
  opacity: 0.75;
}
.hf-tab.active {
  opacity: 1;
  background: rgba(255,255,255,0.85);
  box-shadow: 0 1px 8px rgba(0,0,0,0.06);
}
@media (prefers-color-scheme: dark) {
  .hf-tab.active { background: rgba(15,23,42,0.60); }
}

#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
</style>
"""


def _placeholder_image() -> bytes:
    width, height = 1280, 720
    img = Image.new("RGB", (width, height), color=(20, 24, 35))
    draw = ImageDraw.Draw(img)
    text = "Scene Image"
    font = ImageFont.load_default()
    text_width, text_height = draw.textsize(text, font=font)
    draw.text(
        ((width - text_width) / 2, (height - text_height) / 2),
        text,
        font=font,
        fill=(230, 233, 240),
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_sidebar() -> None:
    st.sidebar.markdown("### History Forge")
    st.sidebar.caption("Project Settings")
    st.sidebar.button("Topic", use_container_width=True)
    st.sidebar.button("Length", use_container_width=True)
    st.sidebar.button("Tone", use_container_width=True)
    st.sidebar.button("Visual Style", use_container_width=True)

    st.sidebar.divider()
    st.sidebar.caption("Assets")
    st.sidebar.button("Media Library", use_container_width=True)
    st.sidebar.button("Sources", use_container_width=True)

    st.sidebar.divider()
    st.sidebar.caption("Appearance")
    st.sidebar.info("Uses your system light/dark mode.\n\n(Optionally add a manual toggle later.)")


def render_header(story_title: str) -> None:
    st.markdown(
        f"""
        <div class="hf-toolbar">
          <div style="display:flex;flex-direction:column;gap:2px;">
            <div style="font-weight:700; font-size:18px;">{story_title}</div>
            <div class="hf-tabs">
              <div class="hf-tab">Script</div>
              <div class="hf-tab active">Scenes</div>
              <div class="hf-tab">Export</div>
            </div>
          </div>
          <div style="display:flex;gap:10px;">
            <div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns([1, 1])
    with c1:
        st.button("Save Draft", use_container_width=True)
    with c2:
        st.button("Generate Package", type="primary", use_container_width=True)


def _iter_scenes() -> Iterable[Scene]:
    return sorted(st.session_state.get("scenes", []), key=lambda s: s.index)


def _scene_image_bytes(scene: Scene) -> bytes:
    primary = _get_primary_image(scene)
    return primary if primary else _placeholder_image()


def _scene_prompt(scene: Scene) -> str:
    return scene.image_prompt or ""


def render_scene_card(scene: Scene) -> None:
    scene_id = f"scene_{scene.index}"
    prompt_key = f"prompt_{scene_id}"

    st.markdown('<div class="hf-card">', unsafe_allow_html=True)

    st.markdown(
        f"""
        <div style="position:relative;">
          <div style="position:absolute; top:10px; left:10px; z-index:5;">
            <span class="hf-chip">Scene {scene.index:02d}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.image(_scene_image_bytes(scene), use_container_width=True)

    st.markdown('<div class="hf-card-body">', unsafe_allow_html=True)
    st.markdown(f"**{scene.title or 'Scene'}**")

    if prompt_key not in st.session_state:
        st.session_state[prompt_key] = _scene_prompt(scene)

    st.text_area(
        "Image prompt",
        key=prompt_key,
        label_visibility="collapsed",
        height=90,
        placeholder="Enter image prompt...",
    )

    a1, a2, a3 = st.columns([1, 1, 1])
    with a1:
        if st.button("Regenerate image", key=f"regen_{scene_id}", use_container_width=True):
            scene.image_prompt = st.session_state[prompt_key]
            aspect_ratio = st.session_state.get("aspect_ratio", "16:9")
            visual_style = st.session_state.get("visual_style", "Photorealistic cinematic")
            updated = generate_image_for_scene(scene, aspect_ratio=aspect_ratio, visual_style=visual_style)
            scene.image_bytes = updated.image_bytes
            if scene.image_bytes:
                scene.image_variations.append(scene.image_bytes)
                scene.primary_image_index = len(scene.image_variations) - 1
            scene.image_error = updated.image_error
            st.toast(f"Regenerating image for Scene {scene.index:02d}…")
            st.rerun()
    with a2:
        if st.button("Save prompt", key=f"save_{scene_id}", use_container_width=True):
            scene.image_prompt = st.session_state[prompt_key]
            st.toast(f"Saved prompt for Scene {scene.index:02d}")
    with a3:
        if st.button("Delete", key=f"delete_{scene_id}", use_container_width=True):
            st.session_state.scenes = [s for s in st.session_state.scenes if s.index != scene.index]
            _sync_scene_order(st.session_state.scenes)
            st.rerun()

    if scene.image_error:
        st.warning(scene.image_error)

    if scene.image_variations:
        with st.expander("Variations"):
            st.image([img for img in scene.image_variations if img], use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def render_scene_grid(scenes: Iterable[Scene]) -> None:
    st.markdown(
        """
        <div class="hf-header">
          <div class="hf-title">Visual Storyboard</div>
          <div class="hf-subtitle hf-muted">Review and refine AI-generated scenes for your script.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    scene_list = list(scenes)
    if not scene_list:
        st.info("Generate scenes from the main page to see the storyboard here.")
        return

    cols = st.columns(3)
    for i, scene in enumerate(scene_list):
        with cols[i % 3]:
            render_scene_card(scene)

    st.divider()
    if st.button("➕ Add New Scene", use_container_width=True):
        next_index = len(scene_list) + 1
        new_scene = Scene(
            index=next_index,
            title=f"Scene {next_index}",
            script_excerpt="",
            visual_intent="",
            image_prompt="",
        )
        st.session_state.setdefault("scenes", []).append(new_scene)
        st.toast("Add New Scene clicked")
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Storyboard", layout="wide")
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
    require_login()

    render_sidebar()

    story_title = st.session_state.get("active_story_title", "Untitled Project")
    render_header(story_title)

    render_scene_grid(_iter_scenes())


if __name__ == "__main__":
    main()
