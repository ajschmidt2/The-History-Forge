"""AI Video Generator tab — Google Veo and OpenAI Sora.

Allows the user to enter a text prompt, choose a provider (Veo or Sora),
generate a video (30 – 120 s), and watch it directly inside the app.

The generated video is automatically:
  • Uploaded to the ``generated-videos`` Supabase bucket.
  • Recorded in the ``assets`` table with the project ID, prompt, and provider.
"""
from __future__ import annotations

import streamlit as st

from src.ai_video_generation import generate_video, sora_configured, veo_configured
from src.ui.state import active_project_id


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


# ---------------------------------------------------------------------------
# Session-state keys used by this tab
# ---------------------------------------------------------------------------
_KEY_RESULT_URL = "ai_video_result_url"
_KEY_RESULT_PROMPT = "ai_video_result_prompt"
_KEY_RESULT_PROVIDER = "ai_video_result_provider"
_KEY_ERROR = "ai_video_error"


def _reset_result() -> None:
    for key in (_KEY_RESULT_URL, _KEY_RESULT_PROMPT, _KEY_RESULT_PROVIDER, _KEY_ERROR):
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
        st.error(
            "**No video generation providers are configured.**\n\n"
            "Add credentials to `.streamlit/secrets.toml`:\n"
            "- **Google Veo**: set `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, "
            "and `GOOGLE_ACCESS_TOKEN`.\n"
            "- **OpenAI Sora**: set `openai_api_key`.\n\n"
            "See `SUPABASE_SETUP.md` → *Section 6* for details."
        )

        # Still show individual provider status for easier debugging
        with st.expander("Provider credential status"):
            veo_ok = veo_configured()
            sora_ok = sora_configured()
            st.markdown(
                f"- Google Veo: {'✅ configured' if veo_ok else '❌ missing credentials'}\n"
                f"- OpenAI Sora: {'✅ configured' if sora_ok else '❌ missing credentials'}"
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

    # Show a note when only one provider is available
    if len(available) == 1:
        missing = "Sora (OpenAI)" if provider_key == "veo" else "Veo (Google)"
        st.info(
            f"**{missing}** credentials are not configured — "
            "only the provider above is available."
        )

    st.divider()

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

        with st.spinner(
            f"Generating video with {provider_label} — this can take 30 – 120 seconds…"
        ):
            try:
                url = generate_video(
                    prompt=prompt.strip(),
                    provider=provider_key,
                    project_id=active_project_id(),
                )
                st.session_state[_KEY_RESULT_URL] = url
                st.session_state[_KEY_RESULT_PROMPT] = prompt.strip()
                st.session_state[_KEY_RESULT_PROVIDER] = provider_label
                st.session_state.pop(_KEY_ERROR, None)
            except (ValueError, PermissionError) as exc:
                # Configuration / auth problems — show as a warning (user-fixable)
                st.session_state[_KEY_ERROR] = ("warning", str(exc))
            except TimeoutError as exc:
                st.session_state[_KEY_ERROR] = ("warning", str(exc))
            except Exception as exc:  # noqa: BLE001
                # Unexpected API / network errors
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
    if result_url:
        st.success(
            f"Video generated with **{st.session_state.get(_KEY_RESULT_PROVIDER, provider_label)}**!"
        )

        # HTML5 video player — works for both http(s):// and data: URLs
        if result_url.startswith("data:"):
            st.video(result_url)
            st.info(
                "Supabase is not configured, so the video is displayed inline only.  "
                "Right-click the player to save it locally."
            )
        else:
            st.video(result_url)
            st.markdown(f"**Stored URL:** [{result_url}]({result_url})")

        st.caption(
            f"Prompt used: *{st.session_state.get(_KEY_RESULT_PROMPT, '')}*"
        )

        if st.button("Generate another video", use_container_width=False):
            _reset_result()
            st.rerun()

    # ------------------------------------------------------------------
    # History: previously generated videos for this project
    # ------------------------------------------------------------------
    _render_history()


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
    st.subheader("Previously generated videos")
    for row in rows:
        label = row.get("filename", "video")
        url = row.get("url", "")
        created = row.get("created_at", "")[:10]
        with st.expander(f"{label}  ({created})", expanded=False):
            if url:
                st.video(url)
                st.markdown(f"[Open in new tab]({url})")
