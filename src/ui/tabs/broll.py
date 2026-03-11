"""Streamlit tab – 🎞️ B-Roll.

Allows users to search, preview, assign, and manage free stock video B-roll
clips for each scene in a History Forge project.

Provider priority (configurable):
  1. Pexels  – free, 200 req/hr, 20 000 req/month
  2. Pixabay – free, 100 req/60s, 24-hour cache required

Usage:
  from src.ui.tabs.broll import tab_broll
  with tabs[N]:
      tab_broll(active_project_id())
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import streamlit as st

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_scenes() -> list[Any]:
    return list(st.session_state.get("scenes", []) or [])


def _active_aspect_ratio() -> str:
    return str(st.session_state.get("aspect_ratio", "16:9") or "16:9")


def _provider_priority(preferred: str) -> list[str]:
    mapping: dict[str, list[str]] = {
        "Pexels": ["pexels"],
        "Pixabay": ["pixabay"],
        "Pexels then Pixabay": ["pexels", "pixabay"],
        "Pixabay then Pexels": ["pixabay", "pexels"],
    }
    return mapping.get(preferred, ["pexels", "pixabay"])


def _broll_settings_key(project_id: str) -> str:
    return f"broll_settings_{project_id}"


def _load_broll_settings(project_id: str) -> dict:
    key = _broll_settings_key(project_id)
    if key not in st.session_state:
        st.session_state[key] = {
            "enable_broll": False,
            "auto_search": False,
            "auto_assign_first": False,
            "preferred_provider": "Pexels then Pixabay",
        }
    return st.session_state[key]


def _save_broll_settings(project_id: str, settings: dict) -> None:
    st.session_state[_broll_settings_key(project_id)] = settings


def _search_results_key(scene_index: int) -> str:
    return f"broll_results_{scene_index}"


def _api_keys_configured() -> dict[str, bool]:
    """Return which B-roll API keys are present."""
    from src.broll.config import broll_provider_status
    return broll_provider_status()


def _render_api_key_status(provider_priority: list[str]) -> dict[str, bool]:
    status = _api_keys_configured()
    cols = st.columns(2)
    with cols[0]:
        st.success("Pexels configured: yes") if status["pexels"] else st.warning("Pexels configured: no")
    with cols[1]:
        st.success("Pixabay configured: yes") if status["pixabay"] else st.warning("Pixabay configured: no")

    if not status["pexels"]:
        st.info("Pexels API key not found. Expected secret name: `PEXELS_API_KEY`.")
    if not status["pixabay"]:
        st.info("Pixabay API key not found. Expected secret name: `PIXABAY_API_KEY`.")

    if not any(status.values()):
        st.error("No B-roll provider keys are configured. Add `PEXELS_API_KEY` and/or `PIXABAY_API_KEY`, then restart Streamlit.")

    with st.expander("B-roll provider diagnostics", expanded=False):
        st.write(f"Pexels secret visible: {'yes' if status['pexels'] else 'no'}")
        st.write(f"Pixabay secret visible: {'yes' if status['pixabay'] else 'no'}")
        first_provider = provider_priority[0] if provider_priority else "pexels"
        st.write(f"Provider priority: {', '.join(provider_priority)}")
        st.write(f"First provider to try: {first_provider}")
        st.caption("If you changed `.streamlit/secrets.toml`, restart the app to load new values.")

    return status


def _scene_has_broll(scene: Any) -> bool:
    return bool(getattr(scene, "use_broll", False)) and bool(getattr(scene, "broll_local_path", ""))


def _scene_has_ai_video(scene: Any) -> bool:
    vp = str(getattr(scene, "video_path", "") or "")
    return bool(vp) and Path(vp).exists()


# ---------------------------------------------------------------------------
# Per-scene B-roll UI
# ---------------------------------------------------------------------------

def _render_scene_broll_card(scene: Any, settings: dict, project_id: str) -> None:
    """Render the search/preview/assign controls for a single scene."""
    from src.broll.service import (
        assign_broll_to_scene,
        clear_broll_from_scene,
        generate_broll_query_for_scene,
    )

    idx = int(scene.index)
    scene_label = f"Scene {idx}: {str(getattr(scene, 'title', '') or '').strip() or '(untitled)'}"

    with st.expander(scene_label, expanded=False):
        status_cols = st.columns([3, 1])
        with status_cols[0]:
            # Show current B-roll status
            if _scene_has_broll(scene):
                st.success(
                    f"B-roll assigned ({scene.broll_provider}) — "
                    f"{scene.broll_duration_sec:.1f}s | {scene.broll_orientation}"
                )
            elif _scene_has_ai_video(scene):
                st.info("Using AI-generated video (B-roll not active)")
            else:
                st.caption("No B-roll assigned – using scene image")

        with status_cols[1]:
            if _scene_has_broll(scene):
                if st.button("Clear B-roll", key=f"broll_clear_{idx}", type="secondary"):
                    clear_broll_from_scene(scene)
                    if _search_results_key(idx) in st.session_state:
                        del st.session_state[_search_results_key(idx)]
                    st.rerun()

        # Show attribution info if B-roll is assigned
        if _scene_has_broll(scene) and getattr(scene, "broll_page_url", ""):
            st.caption(
                f"Source: [{scene.broll_provider.title()}]({scene.broll_page_url}) — "
                f"free for personal and commercial use."
            )

        st.divider()

        # Query field
        default_query = str(getattr(scene, "broll_query", "") or "").strip()
        if not default_query:
            default_query = generate_broll_query_for_scene(scene)

        query = st.text_input(
            "Search query",
            value=default_query,
            key=f"broll_query_input_{idx}",
            placeholder="e.g. ancient Rome soldiers battle",
            help="Keywords used to search Pexels / Pixabay for B-roll clips.",
        )

        aspect_ratio = _active_aspect_ratio()
        provider_names = _provider_priority(settings.get("preferred_provider", "Pexels then Pixabay"))

        provider_status = _api_keys_configured()
        providers_available = any(provider_status.values())

        search_col, _ = st.columns([1, 3])
        with search_col:
            do_search = st.button("Search B-roll", key=f"broll_search_{idx}", type="primary", disabled=not providers_available)

        if do_search and query.strip():
            from src.broll.service import get_last_search_errors, search_broll
            with st.spinner(f"Searching {', '.join(p.title() for p in provider_names)}..."):
                results = search_broll(
                    query.strip(),
                    aspect_ratio=aspect_ratio,
                    per_page=6,
                    provider_priority=provider_names,
                )
                st.session_state[_search_results_key(idx)] = results
                if not results:
                    errors = get_last_search_errors()
                    if errors:
                        for err in errors:
                            st.warning(err)
                    else:
                        st.warning("No B-roll results found for this scene.")

        if not providers_available:
            st.warning("Search disabled until at least one provider key is configured.")

        # Show cached results
        cached_results = st.session_state.get(_search_results_key(idx), [])
        if cached_results:
            st.write(f"**{len(cached_results)} result(s) found:**")
            for result_idx, result in enumerate(cached_results):
                _render_result_card(scene, result, result_idx, project_id)


def _render_result_card(scene: Any, result: Any, result_idx: int, project_id: str) -> None:
    """Render a single B-roll search result with thumbnail and assign button."""
    from src.broll.service import assign_broll_to_scene, download_broll_asset

    idx = int(scene.index)
    result_key = f"broll_result_{idx}_{result_idx}"

    with st.container():
        cols = st.columns([1, 3, 1])

        with cols[0]:
            if result.preview_image_url:
                try:
                    st.image(result.preview_image_url, use_container_width=True)
                except Exception:
                    st.caption("(preview unavailable)")
            else:
                st.caption("No preview")

        with cols[1]:
            st.markdown(
                f"**{result.provider.title()}** — {result.duration_sec:.1f}s — "
                f"{result.width}×{result.height} — {result.orientation}"
            )
            st.caption(result.title[:120] if result.title else "")
            st.caption(result.license_note)

        with cols[2]:
            already_assigned = (
                bool(getattr(scene, "use_broll", False))
                and str(getattr(scene, "broll_source_url", "")) == result.video_url
            )
            btn_label = "Assigned" if already_assigned else "Assign"
            btn_disabled = already_assigned

            if st.button(
                btn_label,
                key=result_key,
                disabled=btn_disabled,
                type="primary" if not already_assigned else "secondary",
            ):
                with st.spinner("Downloading clip…"):
                    try:
                        local_path = download_broll_asset(project_id, idx, result)
                        assign_broll_to_scene(scene, result, local_path)
                        # Persist query
                        scene.broll_query = st.session_state.get(f"broll_query_input_{idx}", "")
                        st.success(f"B-roll assigned to Scene {idx}!")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Download failed: {exc}")

        st.divider()


# ---------------------------------------------------------------------------
# Automation helper
# ---------------------------------------------------------------------------

def run_broll_automation(project_id: str, scenes: list[Any], settings: dict) -> tuple[int, int]:
    """Auto-search and optionally auto-assign B-roll for eligible scenes.

    A scene is eligible for auto-assignment if it does not already have:
    - manually assigned B-roll (use_broll=True)
    - an AI-generated video clip
    - an effects clip

    Parameters
    ----------
    project_id:
        Active project ID.
    scenes:
        Current session scenes list.
    settings:
        B-roll automation settings dict (from ``_load_broll_settings``).

    Returns
    -------
    tuple[int, int]
        (scenes_searched, scenes_assigned)
    """
    from src.broll.service import (
        assign_broll_to_scene,
        download_broll_asset,
        search_broll_for_scene,
    )

    aspect_ratio = _active_aspect_ratio()
    provider_priority = _provider_priority(settings.get("preferred_provider", "Pexels then Pixabay"))
    auto_assign = bool(settings.get("auto_assign_first", False))

    searched = 0
    assigned = 0

    for scene in scenes:
        idx = int(getattr(scene, "index", 0) or 0)
        if idx <= 0:
            continue

        # Skip scenes that already have a higher-priority media source
        if _scene_has_broll(scene):
            continue
        if _scene_has_ai_video(scene):
            continue

        # Check for effects clip
        effects_dir = Path("data/projects") / str(project_id) / "assets" / "effects_clips"
        effects_path = effects_dir / f"s{idx:02d}_effects.mp4"
        if effects_path.exists():
            continue

        try:
            results = search_broll_for_scene(
                scene,
                aspect_ratio=aspect_ratio,
                per_page=3,
                provider_priority=provider_priority,
            )
            searched += 1

            if results:
                st.session_state[_search_results_key(idx)] = results

            if auto_assign and results:
                local_path = download_broll_asset(project_id, idx, results[0])
                assign_broll_to_scene(scene, results[0], local_path)
                assigned += 1

        except Exception as exc:
            logger.warning("B-roll automation failed for scene %d: %s", idx, exc)
            continue

    return searched, assigned


# ---------------------------------------------------------------------------
# Main tab entry point
# ---------------------------------------------------------------------------

def tab_broll(project_id: str) -> None:
    """Render the 🎞️ B-Roll tab."""
    st.header("🎞️ Free B-Roll")
    st.caption(
        "Search, preview, and assign free stock video clips to your scenes. "
        "B-roll replaces the scene image during rendering, keeping the voiceover intact."
    )

    if not project_id:
        st.info("Select or create a project to get started.")
        return

    scenes = _get_scenes()
    settings = _load_broll_settings(project_id)

    provider_names = _provider_priority(settings.get("preferred_provider", "Pexels then Pixabay"))

    # -------------------------------------------------------------------
    # API key status
    # -------------------------------------------------------------------
    provider_status = _render_api_key_status(provider_names)
    st.divider()

    # -------------------------------------------------------------------
    # Automation settings
    # -------------------------------------------------------------------
    with st.expander("⚙️ Automation Settings", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            settings["enable_broll"] = st.checkbox(
                "Enable Free B-roll",
                value=bool(settings.get("enable_broll", False)),
                help="Master switch: activates B-roll features for this project.",
                key=f"broll_enable_{project_id}",
            )
            settings["auto_search"] = st.checkbox(
                "Auto-search B-roll for scenes",
                value=bool(settings.get("auto_search", False)),
                help="Automatically search for B-roll clips during automation runs.",
                key=f"broll_auto_search_{project_id}",
            )
            settings["auto_assign_first"] = st.checkbox(
                "Auto-assign first acceptable result",
                value=bool(settings.get("auto_assign_first", False)),
                help=(
                    "Assign the first search result to each eligible scene automatically. "
                    "Only applies to scenes without manual B-roll, AI video, or effects clips."
                ),
                key=f"broll_auto_assign_{project_id}",
            )
        with col2:
            provider_options = [
                "Pexels then Pixabay",
                "Pexels",
                "Pixabay",
                "Pixabay then Pexels",
            ]
            current_provider = settings.get("preferred_provider", "Pexels then Pixabay")
            if current_provider not in provider_options:
                current_provider = "Pexels then Pixabay"
            settings["preferred_provider"] = st.selectbox(
                "Preferred provider",
                provider_options,
                index=provider_options.index(current_provider),
                key=f"broll_provider_{project_id}",
            )

        _save_broll_settings(project_id, settings)

        # Run automation button
        st.divider()
        if st.button("Run B-roll automation now", key=f"broll_run_auto_{project_id}", disabled=not any(provider_status.values())):
            if not scenes:
                st.warning("No scenes found. Set up scenes first.")
            else:
                with st.spinner("Searching for B-roll clips…"):
                    searched, assigned = run_broll_automation(project_id, scenes, settings)
                st.success(
                    f"Automation complete: {searched} scene(s) searched, {assigned} auto-assigned."
                )
                st.rerun()

    # -------------------------------------------------------------------
    # Summary banner
    # -------------------------------------------------------------------
    if scenes:
        broll_count = sum(1 for s in scenes if _scene_has_broll(s))
        total = len(scenes)
        st.info(
            f"**{broll_count} / {total}** scenes have B-roll assigned. "
            f"{'All scenes covered!' if broll_count == total else 'Remaining scenes will use images or AI video.'}"
        )

    st.divider()

    # -------------------------------------------------------------------
    # Per-scene cards
    # -------------------------------------------------------------------
    if not scenes:
        st.info(
            "No scenes found. Create scenes in the **🧩 Scenes** tab first, "
            "then return here to assign B-roll."
        )
        return

    if not settings.get("enable_broll", False):
        st.warning(
            "B-roll is disabled. Enable it using the **Automation Settings** panel above "
            "to unlock scene controls."
        )
        # Still show a read-only summary of what's assigned so far
        assigned_scenes = [s for s in scenes if _scene_has_broll(s)]
        if assigned_scenes:
            st.write("**Currently assigned B-roll:**")
            for scene in assigned_scenes:
                st.markdown(
                    f"- Scene {scene.index}: {scene.broll_provider.title()} "
                    f"({scene.broll_duration_sec:.1f}s) — "
                    f"[view source]({scene.broll_page_url})"
                )
        return

    for scene in sorted(scenes, key=lambda s: int(getattr(s, "index", 0) or 0)):
        _render_scene_broll_card(scene, settings, project_id)

    # -------------------------------------------------------------------
    # License reminder
    # -------------------------------------------------------------------
    st.divider()
    with st.expander("License & Attribution Notes"):
        st.markdown(
            """
**Pexels License**
- Free for personal and commercial use.
- No attribution required, but appreciated.
- You may not sell or redistribute the clips as stock media.
- Full terms: [pexels.com/license](https://www.pexels.com/license/)

**Pixabay Content License**
- Free for commercial and personal use.
- No attribution required.
- Resale or redistribution as stock is not permitted.
- Full terms: [pixabay.com/service/license-summary](https://pixabay.com/service/license-summary/)
"""
        )
