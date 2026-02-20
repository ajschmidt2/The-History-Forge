import json
import re
from pathlib import Path

import streamlit as st
import utils as forge_utils
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from utils import Scene

PREFERENCES_PATH = Path("data/user_preferences.json")
PROJECTS_ROOT = Path("data/projects")


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


def _load_saved_voice_id() -> str:
    if not PREFERENCES_PATH.exists():
        return ""
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    voice_id = data.get("voice_id", "") if isinstance(data, dict) else ""
    return str(voice_id).strip()


def save_voice_id(voice_id: str) -> None:
    sanitized = (voice_id or "").strip()
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"voice_id": sanitized}
    PREFERENCES_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def slugify_project_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned or "untitled-project"


def _existing_project_ids() -> list[str]:
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    return sorted([p.name for p in PROJECTS_ROOT.iterdir() if p.is_dir()])


def ensure_project_exists(project_id: str) -> Path:
    normalized = slugify_project_id(project_id)
    project_dir = PROJECTS_ROOT / normalized
    (project_dir / "assets/images").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets/audio").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets/thumbnails").mkdir(parents=True, exist_ok=True)
    return project_dir


def init_state() -> None:
    st.session_state.setdefault("project_title", "Untitled Project")
    st.session_state.setdefault("project_id", "")
    st.session_state.setdefault("project_selector", "")
    st.session_state.setdefault("new_project_title", "")
    st.session_state.setdefault("topic", "")
    st.session_state.setdefault("script_text", "")
    st.session_state.setdefault("script_text_input", "")
    st.session_state.setdefault("pending_script_text_input", "")
    if st.session_state.script_text and not st.session_state.script_text_input:
        st.session_state.script_text_input = st.session_state.script_text

    st.session_state.setdefault("tone", "Documentary")
    st.session_state.setdefault("length", "8–10 minutes")

    st.session_state.setdefault("visual_style", "Photorealistic cinematic")
    st.session_state.setdefault("aspect_ratio", "16:9")
    st.session_state.setdefault("variations_per_scene", 1)

    st.session_state.setdefault("max_scenes", 12)
    st.session_state.setdefault("scenes", [])

    st.session_state.setdefault("voice_id", _load_saved_voice_id())
    st.session_state.setdefault("voiceover_bytes", None)
    st.session_state.setdefault("voiceover_error", None)
    st.session_state.setdefault("voiceover_saved_path", "")
    st.session_state.setdefault("video_title_suggestions", [])
    st.session_state.setdefault("selected_video_title", "")
    st.session_state.setdefault("thumbnail_prompt", "")
    st.session_state.setdefault("thumbnail_bytes", None)
    st.session_state.setdefault("thumbnail_error", None)
    st.session_state.setdefault("thumbnail_saved_path", "")
    st.session_state.setdefault("thumbnail_aspect_ratio", "16:9")
    st.session_state.setdefault("video_description_direction", "")
    st.session_state.setdefault("video_description_text", "")
    st.session_state.setdefault("generated_image_cache", {})

    if not st.session_state.project_id:
        existing = _existing_project_ids()
        if existing:
            st.session_state.project_id = existing[0]
        else:
            st.session_state.project_id = slugify_project_id(st.session_state.project_title)
            ensure_project_exists(st.session_state.project_id)


def active_project_id() -> str:
    return slugify_project_id(st.session_state.get("project_id", ""))


def render_project_selector() -> None:
    existing = _existing_project_ids()
    new_label = "➕ New project"
    options = existing + [new_label]

    current = active_project_id()
    if current and current not in existing:
        options = [current] + options
    default_value = current if current in options else (options[0] if options else new_label)

    selected_option = st.selectbox(
        "Create / Select Project",
        options,
        index=options.index(default_value),
        key="project_selector",
        help="All generated assets are saved under data/projects/<project_id>/...",
    )

    if selected_option == new_label:
        st.session_state.new_project_title = st.text_input(
            "New project title",
            value=st.session_state.new_project_title or st.session_state.project_title,
            key="new_project_title",
            placeholder="e.g., The Rise of Rome",
        )
        if st.button("Create and use project", width="stretch"):
            new_title = (st.session_state.new_project_title or "").strip()
            if not new_title:
                st.warning("Enter a project title first.")
                return
            project_id = slugify_project_id(new_title)
            ensure_project_exists(project_id)
            st.session_state.project_id = project_id
            st.session_state.project_title = new_title
            st.session_state.project_selector = project_id
            st.toast(f"Using project: {project_id}")
            st.rerun()
    else:
        selected = slugify_project_id(selected_option)
        if selected != current:
            st.session_state.project_id = selected
            st.toast(f"Switched to project: {selected}")
            st.rerun()


def scenes_ready() -> bool:
    return isinstance(st.session_state.scenes, list) and len(st.session_state.scenes) > 0


def script_ready() -> bool:
    return bool((st.session_state.script_text or "").strip())


def clear_downstream(after: str) -> None:
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

    if after in ("script", "scenes", "prompts"):
        if isinstance(st.session_state.scenes, list):
            for s in st.session_state.scenes:
                if isinstance(s, Scene):
                    s.image_bytes = None
                    s.image_variations = []
                    s.primary_image_index = 0
                    s.image_error = ""


def openai_error_message(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return (
            "OpenAI authentication failed. Check that your API key is valid and set as "
            "`openai_api_key` (or `OPENAI_API_KEY`) in Streamlit secrets."
        )
    if isinstance(exc, RateLimitError):
        return (
            "OpenAI rate limit or quota exceeded. Verify your usage limits and billing status."
        )
    if isinstance(exc, APIConnectionError):
        return "OpenAI connection failed. Please check your network and try again."
    if isinstance(exc, APIError):
        return f"OpenAI API error: {exc}"
    return f"OpenAI request failed: {exc}"


def _generate_video_description_fallback(topic: str, title: str, direction: str, hashtag_count: int) -> str:
    base = (title or topic or "This history story").strip()
    creator_direction = (direction or "").strip()
    direction_line = f" Angle: {creator_direction}" if creator_direction else ""
    hashtags = ["#History", "#Documentary", "#Storytelling", "#WorldHistory", "#HistoricalFacts"]
    hashtags_text = " ".join(hashtags[: max(1, min(hashtag_count, len(hashtags)))])
    return (
        f"{base} changed the course of history in ways most people never hear about. "
        "In this episode, we break down the key events, major figures, and why this story still matters today."
        f"{direction_line}\n\n"
        "If you enjoyed this story, subscribe for more history deep-dives.\n\n"
        f"{hashtags_text}"
    )


def generate_video_description_safe(
    topic: str,
    title: str,
    script: str,
    direction: str,
    hashtag_count: int,
) -> str:
    generator = getattr(forge_utils, "generate_video_description", None)
    if callable(generator):
        return generator(
            topic=topic,
            title=title,
            script=script,
            direction=direction,
            hashtag_count=hashtag_count,
        )
    return _generate_video_description_fallback(topic, title, direction, hashtag_count)
