"""AI Video Generator tab — Gemini/Veo media clips with optional fallbacks.

Allows the user to:
  • Choose a provider and an aspect ratio appropriate for that provider.
  • Enter a text prompt and generate a short image-to-video clip.
  • Watch the result directly inside the app.
  • Load the generated video into any scene in the current project.

The generated video is automatically:
  • Saved locally to ``data/projects/{project_id}/assets/videos/``.
  • Uploaded to the configured Supabase video bucket (when configured).
  • Recorded in the ``assets`` table with the project ID, prompt, and provider.
"""
from __future__ import annotations

from pathlib import Path
import time
from urllib.request import urlopen

import streamlit as st

from src.storage import record_asset
from src.ai_video_generation import generate_video
from src.config.secrets import fal_configured
from src.services.google_veo_video import google_veo_lite_configured
from src.ui.state import active_project_id, ensure_project_exists


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _available_providers() -> list[str]:
    """Return the list of providers whose credentials are present."""
    providers: list[str] = []
    if google_veo_lite_configured():
        providers.append("Gemini Veo Lite")
    if fal_configured():
        providers.append("fal.ai fallback")
    return providers


def _provider_key(label: str) -> str:
    """Convert a display label to the internal provider key."""
    return {
        "Gemini Veo Lite": "google_veo_lite",
        "fal.ai fallback": "falai",
    }.get(label, "google_veo_lite")


def _aspect_ratios_for(provider_key: str) -> list[str]:
    """Return the aspect-ratio options for the given provider key."""
    return ["9:16", "16:9", "1:1"]


def _videos_dir(project_id: str) -> Path:
    return Path("data/projects") / project_id / "assets/videos"


def _persist_video_from_url(project_id: str, source_url: str, stem_hint: str = "video") -> str | None:
    """Download a remote video URL into project assets/videos and return the local path."""
    if not source_url.startswith(("http://", "https://")):
        return None
    videos_dir = _videos_dir(project_id)
    videos_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in stem_hint) or "video"
    destination = videos_dir / f"{safe_stem}_{int(time.time())}.mp4"
    try:
        with urlopen(source_url) as response:
            destination.write_bytes(response.read())
    except Exception:  # noqa: BLE001 - non-fatal fallback if remote download fails
        return None
    record_asset(project_id, "generated_video", destination)
    return str(destination.resolve())


def _saved_videos(project_id: str) -> list[Path]:
    """Return all .mp4 files saved locally for this project, newest first."""
    d = _videos_dir(project_id)
    if not d.exists():
        return []
    return sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)


def _clear_scene_image_asset(scene) -> None:
    """Ensure a scene uses either video or image media, never both."""
    scene.image_bytes = None
    scene.image_variations = []
    project_id = active_project_id()
    image_path = Path("data/projects") / project_id / "assets/images" / f"s{int(scene.index):02d}.png"
    if image_path.exists():
        image_path.unlink(missing_ok=True)


def _assign_video_to_scene(scene, *, local_path: str | None, url: str | None, object_path: str | None = None) -> None:
    """Assign a video clip to a scene and remove any existing image asset."""
    scene.video_path = local_path if local_path and Path(local_path).exists() else None
    scene.video_url = None if scene.video_path else (url if str(url or "").startswith(("http://", "https://")) else None)
    scene.video_object_path = str(object_path or "").strip() or None
    scene.video_loop = False
    scene.video_muted = True
    scene.video_volume = 0.0
    _clear_scene_image_asset(scene)


# ---------------------------------------------------------------------------
# Session-state keys used by this tab
# ---------------------------------------------------------------------------
_KEY_RESULT_URL = "ai_video_result_url"
_KEY_RESULT_LOCAL = "ai_video_result_local_path"
_KEY_RESULT_PROMPT = "ai_video_result_prompt"
_KEY_RESULT_PROVIDER = "ai_video_result_provider"
_KEY_RESULT_RATIO = "ai_video_result_aspect_ratio"
_KEY_ERROR = "ai_video_error"

def _reset_result() -> None:
    for key in (
        _KEY_RESULT_URL,
        _KEY_RESULT_LOCAL,
        _KEY_RESULT_PROMPT,
        _KEY_RESULT_PROVIDER,
        _KEY_RESULT_RATIO,
        _KEY_ERROR,
    ):
        st.session_state.pop(key, None)


# ---------------------------------------------------------------------------
# Main tab renderer
# ---------------------------------------------------------------------------

def tab_ai_video_generator() -> None:
    """Render the AI Video Generator tab."""
    st.subheader("AI Video Generator")
    st.caption(
        "Generate a short image-to-video clip using Gemini Veo Lite, with fal.ai fallback available. "
        "Generated scene images are used as source frames."
    )

    # ------------------------------------------------------------------
    # Credential check — must have at least one provider available
    # ------------------------------------------------------------------
    available = _available_providers()
    if not available:
        st.warning(
            "**No video generation providers are configured.**\n\n"
            "Configure at least one provider in your app secrets (`.streamlit/secrets.toml` "
            "or Streamlit Cloud → App secrets):\n\n"
            "| Provider | Required secret(s) |\n"
            "|---|---|\n"
            "| **Gemini Veo Lite** | `GEMINI_API_KEY` |\n"
            "| **fal.ai fallback** | `fal_api_key` |\n"
            "\nGemini and fal.ai use generated scene images as source frames."
        )

        with st.expander("Credential status detail"):
            gemini_ok = google_veo_lite_configured()
            fal_ok = fal_configured()
            st.markdown(
                "**Gemini Veo Lite** "
                + ("✅ configured" if gemini_ok else "❌ not configured") + "\n"
                + f"  - `GEMINI_API_KEY`: {'✅' if gemini_ok else '❌ missing or placeholder'}\n\n"
                + "**fal.ai fallback** "
                + ("✅ configured" if fal_ok else "❌ not configured") + "\n"
                + f"  - `fal_api_key`: {'✅' if fal_ok else '❌ missing or placeholder'}"
            )
        return

    # ------------------------------------------------------------------
    # Provider selector
    # ------------------------------------------------------------------
    provider_label = st.radio(
        "Video provider",
        options=available,
        horizontal=True,
        help="Only providers with valid credentials are shown.",
    )
    provider_key = _provider_key(provider_label)

    if provider_key in {"google_veo_lite", "falai"}:
        st.info("This provider animates the first generated scene image in the current project.")

    # ------------------------------------------------------------------
    # Aspect-ratio selector — options depend on the chosen provider
    # ------------------------------------------------------------------
    ratio_options = _aspect_ratios_for(provider_key)

    aspect_ratio = st.radio(
        "Aspect ratio",
        options=ratio_options,
        horizontal=True,
        help=(
            "**16:9** — standard landscape (YouTube / desktop).  "
            "**9:16** — vertical / Shorts / Reels.  "
            "**1:1** — square."
        ),
    )

    st.divider()

    # ------------------------------------------------------------------
    # Duration
    # ------------------------------------------------------------------
    clip_seconds = int(
        st.selectbox(
            "Clip length (seconds)",
            options=[4, 5, 6, 8],
            index=1,
            help="Shorter clips keep Gemini video cost lower.",
        )
    )

    # ------------------------------------------------------------------
    # Prompt input
    # ------------------------------------------------------------------
    prompt = st.text_area(
        "Video prompt",
        placeholder=(
            "e.g. A dramatic slow-motion shot of ancient Roman soldiers "
            "marching through a misty forest at dawn."
        ),
        height=120,
        help="Describe the scene you want the AI to generate as a short video clip.",
    )

    # ------------------------------------------------------------------
    # Generate button
    # ------------------------------------------------------------------
    can_generate = bool(prompt.strip())
    if st.button(
        f"Generate video with {provider_label}",
        type="primary",
        disabled=not can_generate,
        help="Enter a prompt above to enable generation." if not can_generate else None,
    ):
        _reset_result()
        project_id = active_project_id()
        ensure_project_exists(project_id)
        save_dir = _videos_dir(project_id)

        with st.spinner(f"Generating video with {provider_label} ({aspect_ratio}, {clip_seconds}s) - this can take a few minutes..."):
            try:
                url, local_path = generate_video(
                    prompt=prompt.strip(),
                    provider=provider_key,
                    project_id=project_id,
                    aspect_ratio=aspect_ratio,
                    save_dir=save_dir,
                    seconds=clip_seconds,
                )
                st.session_state[_KEY_RESULT_URL] = url
                st.session_state[_KEY_RESULT_LOCAL] = local_path
                st.session_state[_KEY_RESULT_PROMPT] = prompt.strip()
                st.session_state[_KEY_RESULT_PROVIDER] = provider_label
                st.session_state[_KEY_RESULT_RATIO] = aspect_ratio
                if local_path and Path(local_path).exists():
                    record_asset(project_id, "generated_video", Path(local_path))
                st.session_state.pop(_KEY_ERROR, None)
            except (ValueError, PermissionError) as exc:
                st.session_state[_KEY_ERROR] = ("warning", str(exc))
            except TimeoutError as exc:
                st.session_state[_KEY_ERROR] = ("warning", str(exc))
            except Exception as exc:  # noqa: BLE001
                st.session_state[_KEY_ERROR] = ("error", str(exc))

        st.rerun()

    # ------------------------------------------------------------------
    # Result / error display
    # ------------------------------------------------------------------
    if error_info := st.session_state.get(_KEY_ERROR):
        severity, message = error_info
        if severity == "warning":
            st.warning(f"**Generation failed:** {message}")
        else:
            st.error(f"**Unexpected error:** {message}")

    result_url = st.session_state.get(_KEY_RESULT_URL)
    result_local = st.session_state.get(_KEY_RESULT_LOCAL)
    if result_url:
        res_provider = st.session_state.get(_KEY_RESULT_PROVIDER, provider_label)
        res_ratio = st.session_state.get(_KEY_RESULT_RATIO, aspect_ratio)
        st.success(f"Video generated with **{res_provider}** ({res_ratio})!")

        if result_url.startswith("data:"):
            st.video(result_url)
            st.info(
                "Supabase is not configured, so the video is displayed inline only.  "
                "Right-click the player to save it locally."
            )
        else:
            st.video(result_url)
            st.markdown(f"**Stored URL:** [{result_url}]({result_url})")

        if result_local:
            st.caption(f"Saved locally: `{result_local}`")

        st.caption(f"Prompt used: *{st.session_state.get(_KEY_RESULT_PROMPT, '')}*")

        # ------------------------------------------------------------------
        # Load into scene
        # ------------------------------------------------------------------
        scenes = st.session_state.get("scenes", [])
        if scenes:
            st.markdown("**Load into scene**")
            scene_labels = [f"Scene {s.index:02d} — {s.title}" for s in scenes]
            col_sel, col_btn = st.columns([3, 1])
            with col_sel:
                chosen_label = st.selectbox(
                    "Pick a scene",
                    scene_labels,
                    key="ai_video_scene_pick",
                    label_visibility="collapsed",
                )
            with col_btn:
                if st.button("Assign to scene", type="secondary", width="stretch"):
                    chosen_idx = scene_labels.index(chosen_label)
                    chosen_scene = scenes[chosen_idx]
                    _assign_video_to_scene(chosen_scene, local_path=result_local, url=result_url, object_path=None)
                    st.toast(
                        f"Video assigned to {chosen_label}. "
                        "Scene image removed so this scene now uses video only."
                    )
                    st.rerun()
        else:
            st.info(
                "No scenes exist yet — split the script into scenes first, "
                "then return here to assign this video to a scene."
            )

        if st.button("Generate another video", width="content"):
            _reset_result()
            st.rerun()

    # ------------------------------------------------------------------
    # Saved videos for this project
    # ------------------------------------------------------------------
    _render_saved_videos()

    # ------------------------------------------------------------------
    # History: previously generated videos for this project (Supabase)
    # ------------------------------------------------------------------
    _render_history()


# ---------------------------------------------------------------------------
# Saved-videos panel (local files)
# ---------------------------------------------------------------------------

def _render_saved_videos() -> None:
    """Show locally saved videos for this project with assign-to-scene controls."""
    project_id = active_project_id()
    videos = _saved_videos(project_id)
    if not videos:
        return

    st.divider()
    st.subheader("Saved videos (this project)")

    scenes = st.session_state.get("scenes", [])
    scene_labels = [f"Scene {s.index:02d} — {s.title}" for s in scenes]

    for vid_path in videos:
        with st.expander(vid_path.name, expanded=False):
            st.video(str(vid_path))
            st.caption(f"`{vid_path}`")

            if scene_labels:
                pick_key = f"saved_vid_scene_pick_{vid_path.name}"
                btn_key = f"saved_vid_assign_{vid_path.name}"
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    chosen = st.selectbox(
                        "Assign to scene",
                        scene_labels,
                        key=pick_key,
                        label_visibility="visible",
                    )
                with col_b:
                    st.write("")  # vertical alignment spacer
                    if st.button("Assign", key=btn_key, width="stretch"):
                        chosen_idx = scene_labels.index(chosen)
                        chosen_scene = scenes[chosen_idx]
                        _assign_video_to_scene(chosen_scene, local_path=str(vid_path), url=None, object_path=None)
                        st.toast(f"Video assigned to {chosen}. Scene image removed.")
                        st.rerun()
            else:
                st.caption("Create scenes first to assign videos to them.")


# ---------------------------------------------------------------------------
# Supabase history panel
# ---------------------------------------------------------------------------

def _render_history() -> None:
    """Show previously generated videos recorded in Supabase for this project."""
    import src.supabase_storage as _sb_store  # local import to avoid circular deps

    if not _sb_store.is_configured():
        return

    sb = _sb_store.get_client()
    if sb is None:
        return

    project_id = active_project_id()
    rows: list[dict] = []
    try:
        resp = (
            sb.table("assets")
            .select("filename, url, created_at")
            .eq("project_id", project_id)
            .eq("asset_type", "generated_video")
            .order("created_at", desc=True)
            .limit(25)
            .execute()
        )
        rows.extend(resp.data or [])
    except Exception:
        pass

    try:
        rows.extend(_sb_store.list_generated_videos(project_id, limit=25))
    except Exception:
        pass

    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("filename") or ""), str(row.get("url") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    rows = sorted(deduped, key=lambda item: str(item.get("created_at") or ""), reverse=True)[:25]

    if not rows:
        return

    st.divider()
    st.subheader("Cloud history (Supabase)")

    scenes = st.session_state.get("scenes", [])
    scene_labels = [f"Scene {s.index:02d} — {s.title}" for s in scenes]

    for row in rows:
        label = row.get("filename", "video")
        url = row.get("url", "")
        created = row.get("created_at", "")[:10]
        with st.expander(f"{label}  ({created})", expanded=False):
            if url:
                st.video(url)
                st.markdown(f"[Open in new tab]({url})")

                if scene_labels:
                    pick_key = f"cloud_vid_scene_pick_{label}"
                    btn_key = f"cloud_vid_assign_{label}"
                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        chosen = st.selectbox(
                            "Assign to scene",
                            scene_labels,
                            key=pick_key,
                            label_visibility="visible",
                        )
                    with col_b:
                        st.write("")
                        if st.button("Assign", key=btn_key, width="stretch"):
                            chosen_idx = scene_labels.index(chosen)
                            chosen_scene = scenes[chosen_idx]
                            local_path = _persist_video_from_url(project_id, url, stem_hint=f"scene_{chosen_scene.index:02d}") if url else None
                            _assign_video_to_scene(chosen_scene, local_path=local_path, url=url, object_path=str(row.get("object_path") or "") or None)
                            if local_path:
                                st.toast(f"Video assigned to {chosen}. Scene image removed.")
                            else:
                                st.toast(f"Video assigned to {chosen} via URL. Scene image removed.")
                            st.rerun()
