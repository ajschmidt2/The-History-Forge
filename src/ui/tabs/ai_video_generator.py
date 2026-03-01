"""AI Video Generator tab — Google Veo and OpenAI Sora.

Allows the user to:
  • Choose a provider (Veo or Sora) and an aspect ratio appropriate for that provider.
  • Enter a text prompt and generate a short video clip (30 – 120 s).
  • Watch the result directly inside the app.
  • Load the generated video into any scene in the current project.

The generated video is automatically:
  • Saved locally to ``data/projects/{project_id}/assets/videos/``.
  • Uploaded to the ``generated-videos`` Supabase bucket (when configured).
  • Recorded in the ``assets`` table with the project ID, prompt, and provider.
"""
from __future__ import annotations

from pathlib import Path
import time

import streamlit as st

from src.ai_video_generation import (
    SORA_ASPECT_RATIOS,
    VEO_ASPECT_RATIOS,
    finalize_sora_video_job,
    generate_video,
    poll_sora_video_job_status,
    sora_configured,
    start_sora_video_job,
    veo_configured,
)
from src.ui.state import active_project_id, ensure_project_exists


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _available_providers() -> list[str]:
    """Return the list of providers whose credentials are present."""
    providers: list[str] = []
    if veo_configured():
        providers.append("Veo (Google)")
    if sora_configured():
        providers.append("Sora (OpenAI)")
    return providers


def _provider_key(label: str) -> str:
    """Convert a display label like 'Veo (Google)' to the internal key 'veo'."""
    return label.split("(")[0].strip().lower()


def _aspect_ratios_for(provider_key: str) -> list[str]:
    """Return the aspect-ratio options for the given provider key."""
    if provider_key == "veo":
        return VEO_ASPECT_RATIOS
    if provider_key == "sora":
        return SORA_ASPECT_RATIOS
    return ["16:9"]


def _videos_dir(project_id: str) -> Path:
    return Path("data/projects") / project_id / "assets/videos"


def _saved_videos(project_id: str) -> list[Path]:
    """Return all .mp4 files saved locally for this project, newest first."""
    d = _videos_dir(project_id)
    if not d.exists():
        return []
    return sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# Session-state keys used by this tab
# ---------------------------------------------------------------------------
_KEY_RESULT_URL = "ai_video_result_url"
_KEY_RESULT_LOCAL = "ai_video_result_local_path"
_KEY_RESULT_PROMPT = "ai_video_result_prompt"
_KEY_RESULT_PROVIDER = "ai_video_result_provider"
_KEY_RESULT_RATIO = "ai_video_result_aspect_ratio"
_KEY_ERROR = "ai_video_error"

_SORA_MAX_POLLS = 120
_SORA_INITIAL_DELAY_S = 3.0
_SORA_MAX_BACKOFF_S = 12.0


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
        "Generate a short video clip from a text description using Google Veo or OpenAI Sora. "
        "Generation typically takes 30 – 120 seconds."
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
            "| **Google Veo** | `SUPABASE_URL` + `SUPABASE_KEY` (or `SUPABASE_ANON_KEY`) |\n"
            "| **OpenAI Sora** | `openai_api_key` |\n\n"
            "Google service-account credentials for Veo must be stored as **Supabase function "
            "secrets** (not frontend secrets). See `SUPABASE_SETUP.md` → *Section 6*."
        )

        with st.expander("Credential status detail"):
            from src.config import get_secret as _gs
            veo_ok = veo_configured()
            sora_ok = sora_configured()
            has_sb_url = bool(_gs("SUPABASE_URL"))
            has_sb_key = bool(
                _gs("SUPABASE_KEY") or _gs("SUPABASE_ANON_KEY") or _gs("SUPABASE_SERVICE_ROLE_KEY")
            )
            has_oai_key = bool(_gs("openai_api_key"))
            st.markdown(
                "**Google Veo** "
                + ("✅ configured" if veo_ok else "❌ not configured") + "\n"
                + (f"  - `SUPABASE_URL`: {'✅' if has_sb_url else '❌ missing or placeholder'}\n")
                + (f"  - `SUPABASE_KEY`: {'✅' if has_sb_key else '❌ missing or placeholder'}\n\n")
                + "**OpenAI Sora** "
                + ("✅ configured" if sora_ok else "❌ not configured") + "\n"
                + (f"  - `openai_api_key`: {'✅' if has_oai_key else '❌ missing or placeholder'}")
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

    if len(available) == 1:
        missing = "Sora (OpenAI)" if provider_key == "veo" else "Veo (Google)"
        st.info(
            f"**{missing}** credentials are not configured — "
            "only the provider above is available."
        )

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
    sora_seconds = 8
    if provider_key == "sora":
        sora_seconds = int(
            st.selectbox(
                "Clip length (seconds)",
                options=[4, 8, 12],
                index=1,
                help="Sora currently supports 4, 8, or 12 seconds.",
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

        with st.spinner(
            f"Generating video with {provider_label} ({aspect_ratio}, {sora_seconds}s) — "
            "this can take 30 – 120 seconds…"
            if provider_key == "sora"
            else f"Generating video with {provider_label} ({aspect_ratio}) — this can take 30 – 120 seconds…"
        ):
            try:
                if provider_key == "sora":
                    start_payload = start_sora_video_job(
                        prompt=prompt.strip(),
                        seconds=sora_seconds,
                        size={"16:9": "1280x720", "9:16": "720x1280", "1:1": "1080x1080"}.get(aspect_ratio, "1280x720"),
                        model="sora-2",
                    )
                    terminal = {"completed", "failed"}
                    delay_s = _SORA_INITIAL_DELAY_S
                    status_payload: dict | None = None
                    for _attempt in range(1, _SORA_MAX_POLLS + 1):
                        try:
                            status_payload = poll_sora_video_job_status(start_payload["jobId"])
                        except Exception as exc:  # noqa: BLE001
                            if "HTTP 429" in str(exc):
                                time.sleep(delay_s)
                                delay_s = min(delay_s * 1.5, _SORA_MAX_BACKOFF_S)
                                continue
                            raise

                        status = str(status_payload.get("status") or "").lower().strip()
                        if status in terminal:
                            break
                        time.sleep(delay_s)

                    if not status_payload:
                        raise RuntimeError("Sora job status could not be read.")

                    final_status = str(status_payload.get("status") or "").lower().strip()
                    if final_status == "completed":
                        finalized = finalize_sora_video_job(start_payload["jobId"])
                        url = str(finalized["url"])
                        local_path = None
                    elif final_status == "failed":
                        raise RuntimeError(str(status_payload.get("error") or "Sora video generation failed."))
                    else:
                        raise TimeoutError(
                            "Sora is still processing after the maximum polling window. "
                            "Refresh to continue checking this job."
                        )
                else:
                    url, local_path = generate_video(
                        prompt=prompt.strip(),
                        provider=provider_key,
                        project_id=project_id,
                        aspect_ratio=aspect_ratio,
                        save_dir=save_dir,
                        seconds=sora_seconds,
                    )
                st.session_state[_KEY_RESULT_URL] = url
                st.session_state[_KEY_RESULT_LOCAL] = local_path
                st.session_state[_KEY_RESULT_PROMPT] = prompt.strip()
                st.session_state[_KEY_RESULT_PROVIDER] = provider_label
                st.session_state[_KEY_RESULT_RATIO] = aspect_ratio
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
                    chosen_scene.video_url = result_url
                    chosen_scene.video_path = result_local
                    st.toast(
                        f"Video assigned to {chosen_label}. "
                        "Open the Scene Editor tab to review it."
                    )
                    st.rerun()
        else:
            st.info(
                "No scenes exist yet — split the script into scenes first, "
                "then return here to assign this video to a scene."
            )

        if st.button("Generate another video", use_container_width=False):
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
                        chosen_scene.video_path = str(vid_path)
                        chosen_scene.video_url = None  # local-only
                        st.toast(f"Video assigned to {chosen}.")
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
    try:
        resp = (
            sb.table("assets")
            .select("filename, url, created_at")
            .eq("project_id", project_id)
            .eq("asset_type", "generated_video")
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        rows = resp.data or []
    except Exception:
        return

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
                            chosen_scene.video_url = url
                            chosen_scene.video_path = None
                            st.toast(f"Video assigned to {chosen}.")
                            st.rerun()
