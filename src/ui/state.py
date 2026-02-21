import json
import re
import shutil
from pathlib import Path

import streamlit as st
import utils as forge_utils
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from src.storage import delete_project_records
from utils import Scene

PREFERENCES_PATH = Path("data/user_preferences.json")
PROJECTS_ROOT = Path("data/projects")
PROJECT_STATE_FILENAME = "project_state.json"


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


def _matching_project_dirs(project_id_or_name: str) -> list[Path]:
    normalized = slugify_project_id(project_id_or_name)
    matches: list[Path] = []
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    for project_dir in PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        if project_dir.name == project_id_or_name or slugify_project_id(project_dir.name) == normalized:
            matches.append(project_dir)
    return matches


def ensure_project_exists(project_id: str) -> Path:
    normalized = slugify_project_id(project_id)
    project_dir = PROJECTS_ROOT / normalized
    (project_dir / "assets/images").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets/audio").mkdir(parents=True, exist_ok=True)
    (project_dir / "assets/thumbnails").mkdir(parents=True, exist_ok=True)
    return project_dir


def _project_state_path(project_id: str) -> Path:
    return PROJECTS_ROOT / slugify_project_id(project_id) / PROJECT_STATE_FILENAME


def _scene_to_serializable(scene: Scene) -> dict[str, object]:
    return {
        "index": int(scene.index),
        "title": str(scene.title or ""),
        "script_excerpt": str(scene.script_excerpt or ""),
        "visual_intent": str(scene.visual_intent or ""),
        "image_prompt": str(scene.image_prompt or ""),
        "status": str(scene.status or "active"),
        "estimated_duration_sec": float(getattr(scene, "estimated_duration_sec", 0.0) or 0.0),
    }


def _scene_from_serializable(raw: object, project_id: str) -> Scene | None:
    if not isinstance(raw, dict):
        return None
    try:
        scene_index = int(raw.get("index", 0))
    except (TypeError, ValueError):
        return None
    if scene_index <= 0:
        return None
    scene = Scene(
        index=scene_index,
        title=str(raw.get("title", "") or ""),
        script_excerpt=str(raw.get("script_excerpt", "") or ""),
        visual_intent=str(raw.get("visual_intent", "") or ""),
        image_prompt=str(raw.get("image_prompt", "") or ""),
    )
    scene.status = str(raw.get("status", "active") or "active")
    try:
        scene.estimated_duration_sec = float(raw.get("estimated_duration_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        scene.estimated_duration_sec = 0.0
    saved_image_path = PROJECTS_ROOT / slugify_project_id(project_id) / "assets/images" / f"s{scene.index:02d}.png"
    if saved_image_path.exists():
        try:
            scene.image_bytes = saved_image_path.read_bytes()
            scene.image_variations = [scene.image_bytes]
        except OSError:
            pass
    return scene


def _clear_scene_widget_state() -> None:
    prefixes = (
        "prompt_",
        "title_",
        "txt_",
        "vi_",
        "scene_upload_",
        "regen_",
        "story_title_",
        "story_excerpt_",
        "story_visual_",
        "story_prompt_",
        "story_caption_",
        "story_duration_",
        "bulk_title_",
        "bulk_txt_",
        "bulk_vi_",
    )
    for key in list(st.session_state.keys()):
        if key.startswith(prefixes):
            del st.session_state[key]


def save_project_state(project_id: str) -> None:
    normalized = slugify_project_id(project_id)
    if not normalized:
        return
    ensure_project_exists(normalized)
    scenes = st.session_state.get("scenes", [])
    payload = {
        "project_id": normalized,
        "project_title": str(st.session_state.get("project_title", "") or ""),
        "topic": str(st.session_state.get("topic", "") or ""),
        "script_text": str(st.session_state.get("script_text", "") or ""),
        "tone": str(st.session_state.get("tone", "Documentary") or "Documentary"),
        "length": str(st.session_state.get("length", "8–10 minutes") or "8–10 minutes"),
        "audience": str(st.session_state.get("audience", "General audience") or "General audience"),
        "story_angle": str(st.session_state.get("story_angle", "Balanced overview") or "Balanced overview"),
        "research_brief_text": str(st.session_state.get("research_brief_text", "") or ""),
        "use_research_brief_for_script": bool(st.session_state.get("use_research_brief_for_script", False)),
        "use_web_research": bool(st.session_state.get("use_web_research", False)),
        "research_sources": st.session_state.get("research_sources", []),
        "outline_json_text": str(st.session_state.get("outline_json_text", "") or ""),
        "reading_level": str(st.session_state.get("reading_level", "General") or "General"),
        "pacing": str(st.session_state.get("pacing", "Balanced") or "Balanced"),
        "run_clarity_pass": bool(st.session_state.get("run_clarity_pass", True)),
        "run_retention_pass": bool(st.session_state.get("run_retention_pass", True)),
        "run_safety_pass": bool(st.session_state.get("run_safety_pass", True)),
        "visual_style": str(st.session_state.get("visual_style", "Photorealistic cinematic") or "Photorealistic cinematic"),
        "aspect_ratio": str(st.session_state.get("aspect_ratio", "16:9") or "16:9"),
        "variations_per_scene": int(st.session_state.get("variations_per_scene", 1) or 1),
        "max_scenes": int(st.session_state.get("max_scenes", 12) or 12),
        "scene_wpm": int(st.session_state.get("scene_wpm", 160) or 160),
        "estimated_total_runtime_sec": float(st.session_state.get("estimated_total_runtime_sec", 0.0) or 0.0),
        "scene_transition_types": st.session_state.get("scene_transition_types", []),
        "scenes": [_scene_to_serializable(scene) for scene in scenes if isinstance(scene, Scene)],
    }
    state_path = _project_state_path(normalized)
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_project_state(project_id: str) -> None:
    normalized = slugify_project_id(project_id)
    ensure_project_exists(normalized)

    state_path = _project_state_path(normalized)
    _clear_scene_widget_state()
    st.session_state.storyboard_selected_pos = 0
    if not state_path.exists():
        st.session_state.project_title = normalized.replace("-", " ").title()
        st.session_state.topic = ""
        st.session_state.script_text = ""
        st.session_state.script_text_input = ""
        st.session_state.generated_script_text_input = ""
        st.session_state.pending_script_text_input = ""
        st.session_state.audience = "General audience"
        st.session_state.story_angle = "Balanced overview"
        st.session_state.research_brief_text = ""
        st.session_state.use_research_brief_for_script = False
        st.session_state.use_web_research = False
        st.session_state.research_sources = []
        st.session_state.outline_json_text = ""
        st.session_state.reading_level = "General"
        st.session_state.pacing = "Balanced"
        st.session_state.run_clarity_pass = True
        st.session_state.run_retention_pass = True
        st.session_state.run_safety_pass = True
        st.session_state.scene_wpm = 160
        st.session_state.estimated_total_runtime_sec = 0.0
        st.session_state.scenes = []
        st.session_state.scene_transition_types = []
        return

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return

    st.session_state.project_title = str(raw.get("project_title", normalized.replace("-", " ").title()) or "")
    st.session_state.topic = str(raw.get("topic", "") or "")
    st.session_state.script_text = str(raw.get("script_text", "") or "")
    st.session_state.script_text_input = st.session_state.script_text
    st.session_state.generated_script_text_input = st.session_state.script_text
    st.session_state.pending_script_text_input = ""
    st.session_state.tone = str(raw.get("tone", "Documentary") or "Documentary")
    st.session_state.length = str(raw.get("length", "8–10 minutes") or "8–10 minutes")
    st.session_state.audience = str(raw.get("audience", "General audience") or "General audience")
    st.session_state.story_angle = str(raw.get("story_angle", "Balanced overview") or "Balanced overview")
    st.session_state.research_brief_text = str(raw.get("research_brief_text", "") or "")
    st.session_state.use_research_brief_for_script = bool(raw.get("use_research_brief_for_script", False))
    st.session_state.use_web_research = bool(raw.get("use_web_research", False))
    raw_sources = raw.get("research_sources", [])
    st.session_state.research_sources = raw_sources if isinstance(raw_sources, list) else []
    st.session_state.outline_json_text = str(raw.get("outline_json_text", "") or "")
    st.session_state.reading_level = str(raw.get("reading_level", "General") or "General")
    st.session_state.pacing = str(raw.get("pacing", "Balanced") or "Balanced")
    st.session_state.run_clarity_pass = bool(raw.get("run_clarity_pass", True))
    st.session_state.run_retention_pass = bool(raw.get("run_retention_pass", True))
    st.session_state.run_safety_pass = bool(raw.get("run_safety_pass", True))
    st.session_state.visual_style = str(raw.get("visual_style", "Photorealistic cinematic") or "Photorealistic cinematic")
    st.session_state.aspect_ratio = str(raw.get("aspect_ratio", "16:9") or "16:9")
    st.session_state.variations_per_scene = int(raw.get("variations_per_scene", 1) or 1)
    st.session_state.max_scenes = int(raw.get("max_scenes", 12) or 12)
    st.session_state.scene_wpm = int(raw.get("scene_wpm", 160) or 160)
    st.session_state.estimated_total_runtime_sec = float(raw.get("estimated_total_runtime_sec", 0.0) or 0.0)
    raw_transitions = raw.get("scene_transition_types", [])
    st.session_state.scene_transition_types = raw_transitions if isinstance(raw_transitions, list) else []

    scenes: list[Scene] = []
    for scene_raw in raw.get("scenes", []):
        scene = _scene_from_serializable(scene_raw, normalized)
        if scene:
            scenes.append(scene)
    st.session_state.scenes = scenes


def delete_project(project_id_or_name: str) -> None:
    normalized = slugify_project_id(project_id_or_name)
    for project_dir in _matching_project_dirs(project_id_or_name):
        shutil.rmtree(project_dir, ignore_errors=True)
    delete_project_records(normalized)


def init_state() -> None:
    st.session_state.setdefault("project_title", "Untitled Project")
    st.session_state.setdefault("project_id", "")
    st.session_state.setdefault("project_selector", "")
    st.session_state.setdefault("new_project_title", "")
    st.session_state.setdefault("topic", "")
    st.session_state.setdefault("script_text", "")
    st.session_state.setdefault("script_text_input", "")
    st.session_state.setdefault("generated_script_text_input", "")
    st.session_state.setdefault("pending_script_text_input", "")
    if st.session_state.script_text and not st.session_state.script_text_input:
        st.session_state.script_text_input = st.session_state.script_text
    if st.session_state.script_text and not st.session_state.generated_script_text_input:
        st.session_state.generated_script_text_input = st.session_state.script_text

    st.session_state.setdefault("tone", "Documentary")
    st.session_state.setdefault("length", "8–10 minutes")
    st.session_state.setdefault("audience", "General audience")
    st.session_state.setdefault("story_angle", "Balanced overview")
    st.session_state.setdefault("research_brief_text", "")
    st.session_state.setdefault("use_research_brief_for_script", False)
    st.session_state.setdefault("use_web_research", False)
    st.session_state.setdefault("research_sources", [])
    st.session_state.setdefault("outline_json_text", "")
    st.session_state.setdefault("reading_level", "General")
    st.session_state.setdefault("pacing", "Balanced")
    st.session_state.setdefault("run_clarity_pass", True)
    st.session_state.setdefault("run_retention_pass", True)
    st.session_state.setdefault("run_safety_pass", True)

    st.session_state.setdefault("visual_style", "Photorealistic cinematic")
    st.session_state.setdefault("aspect_ratio", "16:9")
    st.session_state.setdefault("variations_per_scene", 1)

    st.session_state.setdefault("max_scenes", 12)
    st.session_state.setdefault("scene_wpm", 160)
    st.session_state.setdefault("estimated_total_runtime_sec", 0.0)
    st.session_state.setdefault("scenes", [])
    st.session_state.setdefault("scene_transition_types", [])

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
            load_project_state(existing[0])
        else:
            st.session_state.project_id = slugify_project_id(st.session_state.project_title)
            ensure_project_exists(st.session_state.project_id)


def active_project_id() -> str:
    return slugify_project_id(st.session_state.get("project_id", ""))


def render_project_selector() -> None:
    existing = _existing_project_ids()
    new_label = "➕ New project"

    current = active_project_id()
    if existing and current not in existing:
        st.session_state.project_id = existing[0]
        load_project_state(existing[0])
        current = existing[0]

    options = existing + [new_label]
    default_value = current if current in existing else new_label

    if st.session_state.get("project_selector") not in options:
        st.session_state.project_selector = default_value

    selected_option = st.selectbox(
        "Create / Select Project",
        options,
        index=options.index(st.session_state.get("project_selector", default_value)),
        key="project_selector",
        help="All generated assets are saved under data/projects/<project_id>/...",
    )

    if selected_option == new_label:
        default_new_project_title = st.session_state.get("new_project_title") or st.session_state.project_title
        new_project_title_input = st.text_input(
            "New project title",
            value=default_new_project_title,
            key="new_project_title",
            placeholder="e.g., The Rise of Rome",
        )
        if st.button("Create and use project", width="stretch"):
            new_title = (new_project_title_input or "").strip()
            if not new_title:
                st.warning("Enter a project title first.")
                return
            project_id = slugify_project_id(new_title)
            ensure_project_exists(project_id)
            st.session_state.project_id = project_id
            load_project_state(project_id)
            st.session_state.project_title = new_title
            st.toast(f"Using project: {project_id}")
            st.rerun()
    else:
        selected = slugify_project_id(selected_option)
        if selected != current:
            st.session_state.project_id = selected
            load_project_state(selected)
            st.toast(f"Switched to project: {selected}")
            st.rerun()

        st.divider()
        st.caption("Danger zone")
        confirm_key = f"confirm_delete_{selected}"
        confirm_delete = st.checkbox(
            f"I understand deleting '{selected}' removes all saved files and project data.",
            key=confirm_key,
        )
        if st.button("Delete this project", type="secondary", width="stretch", key=f"delete_project_{selected}"):
            if not confirm_delete:
                st.warning("Check the confirmation box before deleting.")
                return
            delete_project(selected_option)
            remaining = _existing_project_ids()
            if remaining:
                st.session_state.project_id = remaining[0]
                load_project_state(remaining[0])
            else:
                fallback = slugify_project_id("Untitled Project")
                st.session_state.project_id = fallback
                st.session_state.project_title = "Untitled Project"
                st.session_state.topic = ""
                st.session_state.script_text = ""
                st.session_state.script_text_input = ""
                st.session_state.generated_script_text_input = ""
                st.session_state.audience = "General audience"
                st.session_state.story_angle = "Balanced overview"
                st.session_state.research_brief_text = ""
                st.session_state.use_research_brief_for_script = False
                st.session_state.use_web_research = False
                st.session_state.research_sources = []
                st.session_state.outline_json_text = ""
                st.session_state.reading_level = "General"
                st.session_state.pacing = "Balanced"
                st.session_state.run_clarity_pass = True
                st.session_state.run_retention_pass = True
                st.session_state.run_safety_pass = True
                st.session_state.scene_wpm = 160
                st.session_state.estimated_total_runtime_sec = 0.0
                st.session_state.scenes = []
                st.session_state.scene_transition_types = []
                ensure_project_exists(fallback)
            st.toast(f"Deleted project: {selected}")
            st.rerun()


def scenes_ready() -> bool:
    return isinstance(st.session_state.scenes, list) and len(st.session_state.scenes) > 0


def script_ready() -> bool:
    return bool((st.session_state.script_text or "").strip())


def clear_downstream(after: str) -> None:
    if after in ("script",):
        st.session_state.scenes = []
        st.session_state.scene_transition_types = []
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
