"""Streamlit tab: 🎬 Video Effects.

Lets users configure and apply a cinematic effects pipeline to every scene
image before the final FFmpeg assembly.

Layout
------
1. **Global Defaults** panel  – sliders / dropdowns for all effects.
2. **Per-Scene Overrides** panel – collapsible expander per scene; each
   control has a "Use global default" checkbox.
3. **Apply Effects & Render Clips** button – processes every scene image,
   uploads the resulting .mp4 clips to Supabase, and prepares them for the
   existing FFmpeg assembly pipeline.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import streamlit as st

from src.ui.state import PROJECTS_ROOT, active_project_id, slugify_project_id
from src.ui.timeline_sync import sync_timeline_for_project
from src.video.effects_config import (
    GlobalEffectsConfig,
    SceneEffectsConfig,
    load_global_config,
    load_scene_configs,
    resolve_config,
    save_global_config,
    save_scene_configs,
)
from src.video.effects_pipeline import (
    VALID_GRADE_STYLES,
    VALID_GRAIN_INTENSITIES,
    VALID_KB_DIRECTIONS,
    apply_effects_chain,
)

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_KB_DIRECTIONS = sorted(VALID_KB_DIRECTIONS)
_GRADE_STYLES = sorted(VALID_GRADE_STYLES)
_GRAIN_INTENSITIES = ["light", "medium", "heavy"]
_EFFECTS_CLIPS_SUBDIR = Path("assets") / "effects_clips"


# ── Helper: get project dir ───────────────────────────────────────────────────

def _project_dir() -> Path:
    return PROJECTS_ROOT / slugify_project_id(active_project_id())


def _effects_clips_dir() -> Path:
    d = _project_dir() / _EFFECTS_CLIPS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _scene_videos_dir(project_id: str) -> Path:
    d = PROJECTS_ROOT / slugify_project_id(project_id) / "assets" / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _apply_effects_clips_to_scene_editor(scenes: list, project_id: str, clips_dir: Path) -> tuple[int, int]:
    """Copy scene-matched effects clips into canonical scene-video slots.

    This makes rendered effects clips immediately visible in the Scene Editor's
    "AI video clip" section by assigning `scene.video_path` for each matching scene.
    """
    assigned = 0
    missing = 0
    videos_dir = _scene_videos_dir(project_id)

    for idx, scene in enumerate(scenes, start=1):
        src = clips_dir / f"s{idx:02d}_effects.mp4"
        if not src.exists():
            missing += 1
            continue
        dest = videos_dir / f"s{idx:02d}.mp4"
        shutil.copy2(src, dest)

        scene.video_path = str(dest.resolve())
        scene.video_url = None
        scene.video_object_path = None
        scene.video_loop = False
        scene.video_muted = True
        scene.video_volume = 0.0
        assigned += 1

    if assigned > 0:
        sync_timeline_for_project(
            project_path=_project_dir(),
            project_id=project_id,
            title=str(st.session_state.get("project_title", "") or ""),
            session_scenes=scenes,
            meta_overrides={"transition_types": st.session_state.get("scene_transition_types", [])},
        )
    return assigned, missing


# ── Global Defaults panel ─────────────────────────────────────────────────────

def _render_global_defaults(cfg: GlobalEffectsConfig) -> GlobalEffectsConfig:
    """Render the Global Defaults panel and return an updated config."""
    st.subheader("Global Defaults", help="Settings applied to every scene unless overridden below.")

    cols = st.columns(2)

    with cols[0]:
        st.markdown("**Ken Burns (Pan & Zoom)**")
        kb_enabled = st.toggle(
            "Enable Ken Burns",
            value=cfg.ken_burns_enabled,
            key="g_kb_enabled",
        )
        kb_dir = st.selectbox(
            "Direction",
            _KB_DIRECTIONS,
            index=_KB_DIRECTIONS.index(cfg.ken_burns_direction)
            if cfg.ken_burns_direction in _KB_DIRECTIONS else 0,
            disabled=not kb_enabled,
            key="g_kb_dir",
        )
        kb_dur = st.slider(
            "Duration (sec)",
            min_value=2.0, max_value=8.0, step=0.5,
            value=float(cfg.ken_burns_duration),
            disabled=not kb_enabled,
            key="g_kb_dur",
        )
        kb_zoom = st.slider(
            "Zoom factor",
            min_value=1.0, max_value=1.5, step=0.01,
            value=float(cfg.ken_burns_zoom_factor),
            disabled=not kb_enabled,
            key="g_kb_zoom",
            help="1.0 = no zoom, 1.2 = 20% crop/zoom, 1.5 = 50% crop.",
        )

        st.markdown("**Fade In / Out**")
        fade_enabled = st.toggle(
            "Enable Fade",
            value=cfg.fade_enabled,
            key="g_fade_enabled",
        )
        fade_in = st.slider(
            "Fade-in duration (sec)",
            min_value=0.0, max_value=2.0, step=0.1,
            value=float(cfg.fade_in_duration),
            disabled=not fade_enabled,
            key="g_fade_in",
        )
        fade_out = st.slider(
            "Fade-out duration (sec)",
            min_value=0.0, max_value=2.0, step=0.1,
            value=float(cfg.fade_out_duration),
            disabled=not fade_enabled,
            key="g_fade_out",
        )

    with cols[1]:
        st.markdown("**Colour Grade**")
        grade_enabled = st.toggle(
            "Enable Colour Grade",
            value=cfg.color_grade_enabled,
            key="g_grade_enabled",
        )
        grade_style = st.selectbox(
            "Style",
            _GRADE_STYLES,
            index=_GRADE_STYLES.index(cfg.color_grade_style)
            if cfg.color_grade_style in _GRADE_STYLES else 0,
            disabled=not grade_enabled,
            key="g_grade_style",
            help="warm = golden/sepia • cool = blue desaturated • neutral = contrast boost • vintage = faded.",
        )

        st.markdown("**Film Grain**")
        grain_enabled = st.toggle(
            "Enable Film Grain",
            value=cfg.film_grain_enabled,
            key="g_grain_enabled",
        )
        grain_intensity = st.selectbox(
            "Intensity",
            _GRAIN_INTENSITIES,
            index=_GRAIN_INTENSITIES.index(cfg.film_grain_intensity)
            if cfg.film_grain_intensity in _GRAIN_INTENSITIES else 1,
            disabled=not grain_enabled,
            key="g_grain_intensity",
        )

        st.markdown("**Map Flyover**")
        map_flyover_enabled = st.toggle(
            "Enable Map Flyover",
            value=cfg.map_flyover_enabled,
            key="g_map_enabled",
            help="When a scene is tagged as a map image, this replaces Ken Burns with a zoom-to-location animation.",
        )
        map_zoom = st.slider(
            "Map zoom factor",
            min_value=1.5, max_value=5.0, step=0.1,
            value=float(cfg.map_zoom_factor),
            disabled=not map_flyover_enabled,
            key="g_map_zoom",
        )

    return GlobalEffectsConfig(
        ken_burns_enabled=kb_enabled,
        ken_burns_direction=kb_dir,
        ken_burns_duration=kb_dur,
        ken_burns_zoom_factor=kb_zoom,
        map_flyover_enabled=map_flyover_enabled,
        map_zoom_factor=map_zoom,
        fade_enabled=fade_enabled,
        fade_in_duration=fade_in,
        fade_out_duration=fade_out,
        color_grade_enabled=grade_enabled,
        color_grade_style=grade_style,
        film_grain_enabled=grain_enabled,
        film_grain_intensity=grain_intensity,
        output_width=cfg.output_width,
        output_height=cfg.output_height,
        output_fps=cfg.output_fps,
    )


# ── Per-scene override panel ───────────────────────────────────────────────────

def _render_scene_override(
    scene_index: int,
    scene_label: str,
    image_path: Optional[Path],
    cfg: SceneEffectsConfig,
    global_cfg: GlobalEffectsConfig,
) -> SceneEffectsConfig:
    """Render one scene's override expander.  Returns updated SceneEffectsConfig."""

    prefix = f"s{scene_index:02d}"

    with st.expander(f"Scene {scene_index}: {scene_label}", expanded=False):
        if image_path and image_path.exists():
            st.image(str(image_path), width=200)

        is_map = st.checkbox(
            "This is a map image (use Map Flyover instead of Ken Burns)",
            value=cfg.is_map_image,
            key=f"{prefix}_is_map",
        )

        tab_kb, tab_fade, tab_grade, tab_grain, tab_map = st.tabs(
            ["Ken Burns", "Fade", "Colour Grade", "Film Grain", "Map Flyover"]
        )

        # ── Ken Burns ──
        with tab_kb:
            use_global_kb = st.checkbox(
                "Use global default", value=cfg.ken_burns_enabled is None, key=f"{prefix}_kb_global"
            )
            kb_enabled = (
                None if use_global_kb
                else st.toggle("Enable", value=global_cfg.ken_burns_enabled if cfg.ken_burns_enabled is None else cfg.ken_burns_enabled, key=f"{prefix}_kb_on")
            )

            use_global_dir = st.checkbox(
                "Use global direction", value=cfg.ken_burns_direction is None, key=f"{prefix}_dir_global"
            )
            kb_dir = (
                None if use_global_dir
                else st.selectbox(
                    "Direction",
                    _KB_DIRECTIONS,
                    index=_KB_DIRECTIONS.index(cfg.ken_burns_direction or global_cfg.ken_burns_direction)
                    if (cfg.ken_burns_direction or global_cfg.ken_burns_direction) in _KB_DIRECTIONS else 0,
                    key=f"{prefix}_kb_dir",
                )
            )

            use_global_dur = st.checkbox(
                "Use global duration", value=cfg.ken_burns_duration is None, key=f"{prefix}_dur_global"
            )
            kb_dur = (
                None if use_global_dur
                else st.slider(
                    "Duration (sec)", 2.0, 8.0, step=0.5,
                    value=float(cfg.ken_burns_duration or global_cfg.ken_burns_duration),
                    key=f"{prefix}_kb_dur",
                )
            )

            use_global_zoom = st.checkbox(
                "Use global zoom", value=cfg.ken_burns_zoom_factor is None, key=f"{prefix}_zoom_global"
            )
            kb_zoom = (
                None if use_global_zoom
                else st.slider(
                    "Zoom factor", 1.0, 1.5, step=0.01,
                    value=float(cfg.ken_burns_zoom_factor or global_cfg.ken_burns_zoom_factor),
                    key=f"{prefix}_kb_zoom",
                )
            )

        # ── Fade ──
        with tab_fade:
            use_global_fade = st.checkbox(
                "Use global default", value=cfg.fade_enabled is None, key=f"{prefix}_fade_global"
            )
            fade_enabled = (
                None if use_global_fade
                else st.toggle("Enable", value=global_cfg.fade_enabled if cfg.fade_enabled is None else cfg.fade_enabled, key=f"{prefix}_fade_on")
            )

            use_global_fi = st.checkbox(
                "Use global fade-in", value=cfg.fade_in_duration is None, key=f"{prefix}_fi_global"
            )
            fade_in = (
                None if use_global_fi
                else st.slider(
                    "Fade-in (sec)", 0.0, 2.0, step=0.1,
                    value=float(cfg.fade_in_duration or global_cfg.fade_in_duration),
                    key=f"{prefix}_fi",
                )
            )

            use_global_fo = st.checkbox(
                "Use global fade-out", value=cfg.fade_out_duration is None, key=f"{prefix}_fo_global"
            )
            fade_out = (
                None if use_global_fo
                else st.slider(
                    "Fade-out (sec)", 0.0, 2.0, step=0.1,
                    value=float(cfg.fade_out_duration or global_cfg.fade_out_duration),
                    key=f"{prefix}_fo",
                )
            )

        # ── Colour grade ──
        with tab_grade:
            use_global_grade = st.checkbox(
                "Use global default", value=cfg.color_grade_enabled is None, key=f"{prefix}_grade_global"
            )
            grade_enabled = (
                None if use_global_grade
                else st.toggle("Enable", value=global_cfg.color_grade_enabled if cfg.color_grade_enabled is None else cfg.color_grade_enabled, key=f"{prefix}_grade_on")
            )

            use_global_style = st.checkbox(
                "Use global style", value=cfg.color_grade_style is None, key=f"{prefix}_style_global"
            )
            grade_style = (
                None if use_global_style
                else st.selectbox(
                    "Style", _GRADE_STYLES,
                    index=_GRADE_STYLES.index(cfg.color_grade_style or global_cfg.color_grade_style)
                    if (cfg.color_grade_style or global_cfg.color_grade_style) in _GRADE_STYLES else 0,
                    key=f"{prefix}_grade_style",
                )
            )

        # ── Film grain ──
        with tab_grain:
            use_global_grain = st.checkbox(
                "Use global default", value=cfg.film_grain_enabled is None, key=f"{prefix}_grain_global"
            )
            grain_enabled = (
                None if use_global_grain
                else st.toggle("Enable", value=global_cfg.film_grain_enabled if cfg.film_grain_enabled is None else cfg.film_grain_enabled, key=f"{prefix}_grain_on")
            )

            use_global_gi = st.checkbox(
                "Use global intensity", value=cfg.film_grain_intensity is None, key=f"{prefix}_gi_global"
            )
            grain_intensity = (
                None if use_global_gi
                else st.selectbox(
                    "Intensity", _GRAIN_INTENSITIES,
                    index=_GRAIN_INTENSITIES.index(cfg.film_grain_intensity or global_cfg.film_grain_intensity)
                    if (cfg.film_grain_intensity or global_cfg.film_grain_intensity) in _GRAIN_INTENSITIES else 1,
                    key=f"{prefix}_gi",
                )
            )

        # ── Map flyover ──
        with tab_map:
            if not is_map:
                st.info("Tag this scene as a map image (checkbox above) to configure flyover settings.")
                map_start = cfg.map_start_coords
                map_end = cfg.map_end_coords
                map_zoom = cfg.map_zoom_factor
            else:
                st.markdown("Coordinates are fractional: (0,0) = top-left, (1,1) = bottom-right.")
                start_x = st.slider(
                    "Start centre X", 0.0, 1.0, step=0.01,
                    value=float(cfg.map_start_coords[0]),
                    key=f"{prefix}_sx",
                )
                start_y = st.slider(
                    "Start centre Y", 0.0, 1.0, step=0.01,
                    value=float(cfg.map_start_coords[1]),
                    key=f"{prefix}_sy",
                )
                end_x = st.slider(
                    "End centre X (zoom target)", 0.0, 1.0, step=0.01,
                    value=float(cfg.map_end_coords[0]),
                    key=f"{prefix}_ex",
                )
                end_y = st.slider(
                    "End centre Y (zoom target)", 0.0, 1.0, step=0.01,
                    value=float(cfg.map_end_coords[1]),
                    key=f"{prefix}_ey",
                )
                map_start = (start_x, start_y)
                map_end = (end_x, end_y)

                use_global_mz = st.checkbox(
                    "Use global map zoom", value=cfg.map_zoom_factor is None, key=f"{prefix}_mz_global"
                )
                map_zoom = (
                    None if use_global_mz
                    else st.slider(
                        "Map zoom factor", 1.5, 5.0, step=0.1,
                        value=float(cfg.map_zoom_factor or global_cfg.map_zoom_factor),
                        key=f"{prefix}_mz",
                    )
                )

    return SceneEffectsConfig(
        ken_burns_enabled=kb_enabled,
        ken_burns_direction=kb_dir,
        ken_burns_duration=kb_dur,
        ken_burns_zoom_factor=kb_zoom,
        is_map_image=is_map,
        map_start_coords=map_start,
        map_end_coords=map_end,
        map_zoom_factor=map_zoom,
        fade_enabled=fade_enabled,
        fade_in_duration=fade_in,
        fade_out_duration=fade_out,
        color_grade_enabled=grade_enabled,
        color_grade_style=grade_style,
        film_grain_enabled=grain_enabled,
        film_grain_intensity=grain_intensity,
    )


# ── Render / upload logic ──────────────────────────────────────────────────────

def _apply_and_upload_clips(
    scenes: list,
    project_id: str,
    global_cfg: GlobalEffectsConfig,
    scene_cfgs: list[SceneEffectsConfig],
    clips_dir: Path,
) -> list[Optional[Path]]:
    """Process every scene image through the effects chain.

    Uploads each finished clip to Supabase (if configured) and returns the
    list of local MP4 paths (``None`` for any scene that failed entirely).
    """
    import src.supabase_storage as _sb

    results: list[Optional[Path]] = []
    num_scenes = len(scenes)

    progress_bar = st.progress(0, text="Preparing…")
    status_container = st.empty()

    images_dir = PROJECTS_ROOT / slugify_project_id(project_id) / "assets" / "images"

    for idx, scene in enumerate(scenes):
        scene_num = idx + 1
        scene_label = getattr(scene, "title", None) or f"Scene {scene_num}"
        image_path = images_dir / f"s{scene_num:02d}.png"

        progress_bar.progress(
            idx / num_scenes,
            text=f"Rendering scene {scene_num}/{num_scenes}: {scene_label}",
        )
        status_container.info(f"Processing **{scene_label}** …")

        if not image_path.exists():
            log.warning("[effects_tab] no image for scene %d (%s), skipping", scene_num, scene_label)
            status_container.warning(f"No image found for scene {scene_num}; skipping.")
            results.append(None)
            continue

        out_path = clips_dir / f"s{scene_num:02d}_effects.mp4"
        scene_cfg = scene_cfgs[idx] if idx < len(scene_cfgs) else SceneEffectsConfig()
        resolved = resolve_config(global_cfg, scene_cfg)

        try:
            ok = apply_effects_chain(image_path, out_path, **resolved)
        except Exception as exc:
            log.exception("[effects_tab] apply_effects_chain raised for scene %d: %s", scene_num, exc)
            ok = False

        if ok and out_path.exists():
            results.append(out_path)
            status_container.success(f"✓ Scene {scene_num} rendered.")

            # Upload to Supabase and record the asset.
            try:
                storage_path = f"{project_id}/effects_clips/s{scene_num:02d}_effects.mp4"
                clip_bytes = out_path.read_bytes()
                clip_url = _sb._upload_bytes(
                    "history-forge-videos",
                    storage_path,
                    clip_bytes,
                    "video/mp4",
                )
                if clip_url:
                    _sb.record_asset(
                        project_id,
                        "effects_clip",
                        out_path.name,
                        clip_url,
                    )
            except Exception as exc:
                log.warning("[effects_tab] Supabase upload failed for scene %d: %s", scene_num, exc)
        else:
            log.error("[effects_tab] effects chain failed for scene %d", scene_num)
            status_container.error(f"Effects failed for scene {scene_num}; the unprocessed image will be used instead.")
            results.append(None)

    progress_bar.progress(1.0, text="All scenes processed.")
    return results


# ── Clip assignment helpers ────────────────────────────────────────────────────

def _clip_effects_badges(
    clip_filename: str,
    global_cfg: GlobalEffectsConfig,
    scene_cfgs: list[SceneEffectsConfig],
) -> list[str]:
    """Derive human-readable effect labels for a clip based on its scene configs."""
    m = re.match(r"s(\d+)_", clip_filename)
    if not m:
        return []
    scene_num = int(m.group(1))
    if scene_num <= 0 or scene_num > len(scene_cfgs):
        resolved = resolve_config(global_cfg)
    else:
        resolved = resolve_config(global_cfg, scene_cfgs[scene_num - 1])

    badges: list[str] = []
    if resolved.get("ken_burns_enabled"):
        direction = str(resolved.get("ken_burns_direction") or "").replace("-", " ").title()
        badges.append(f"Ken Burns ({direction})")
    if resolved.get("color_grade_enabled"):
        style = str(resolved.get("color_grade_style") or "warm").title()
        badges.append(f"{style} Grade")
    if resolved.get("film_grain_enabled"):
        intensity = str(resolved.get("film_grain_intensity") or "medium").title()
        badges.append(f"Film Grain ({intensity})")
    if resolved.get("fade_enabled"):
        badges.append("Fade")
    return badges


@st.cache_data(ttl=120, show_spinner=False)
def _cached_effects_clips(project_id: str) -> list[dict]:
    """Load effects clips from Supabase with a 2-minute TTL."""
    import src.supabase_storage as _sb
    return _sb.list_effects_clips(project_id)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_clip_assignments(project_id: str) -> dict[int, dict]:
    """Load clip assignments from Supabase with a 1-minute TTL."""
    import src.supabase_storage as _sb
    return _sb.load_clip_assignments(project_id)


def _render_assign_clips_section(
    scenes: list,
    project_id: str,
    global_cfg: GlobalEffectsConfig,
    scene_cfgs: list[SceneEffectsConfig],
    clips_dir: Path,
) -> None:
    """Render the '📎 Assign Clips to Scenes' section at the bottom of the tab."""
    import src.supabase_storage as _sb
    from src.video.utils import get_media_duration
    from src.video.clip_thumbnail import get_clip_thumbnail_url

    st.divider()
    st.subheader("📎 Assign Clips to Scenes")
    st.caption(
        "Assign a rendered effects clip to each scene. "
        "One clip can be reused across multiple scenes."
    )

    # ── Build clip inventory ───────────────────────────────────────────────
    # Merge local clips and Supabase clips; local entries enriched with URLs.
    clip_map: dict[str, dict] = {}

    for local_clip in sorted(clips_dir.glob("s??_effects.mp4")):
        fname = local_clip.name
        clip_map[fname] = {
            "filename": fname,
            "url": None,
            "local_path": str(local_clip),
            "storage_path": None,
        }

    if _sb.is_configured():
        for clip in _cached_effects_clips(project_id):
            fname = clip.get("filename", "")
            if not fname:
                continue
            if fname in clip_map:
                clip_map[fname]["url"] = clip.get("url")
                clip_map[fname]["storage_path"] = clip.get("storage_path")
            else:
                clip_map[fname] = {
                    "filename": fname,
                    "url": clip.get("url"),
                    "local_path": None,
                    "storage_path": clip.get("storage_path"),
                }

    all_clips = sorted(clip_map.values(), key=lambda c: c["filename"])

    if not all_clips:
        st.info(
            "No rendered clips found. Use **🎬 Apply Effects & Render Clips** above "
            "to process your scene images first, then return here to assign them."
        )
        return

    # ── Load existing assignments ──────────────────────────────────────────
    if _sb.is_configured():
        assignments: dict[int, dict] = _cached_clip_assignments(project_id)
    else:
        assignments = {}
    # Merge session-local assignments so selectbox reflects them even when
    # the Supabase save failed. Supabase values take precedence.
    local_sess_assignments: dict = st.session_state.get("local_clip_assignments", {})
    merged_assignments: dict[int, dict] = {**local_sess_assignments, **assignments}

    # ── Clip Library grid ──────────────────────────────────────────────────
    with st.expander(f"🎞️ Clip Library ({len(all_clips)} clip(s))", expanded=True):
        lib_cols = st.columns(min(3, max(1, len(all_clips))))
        for idx, clip in enumerate(all_clips):
            fname = clip["filename"]
            local_path = clip.get("local_path")
            clip_url = clip.get("url")
            col = lib_cols[idx % 3]
            with col:
                # Thumbnail (extracted via FFmpeg, cached in session state + Supabase)
                if _sb.is_configured():
                    thumb_key = f"hf_clip_thumb_{project_id}_{fname}"
                    if thumb_key not in st.session_state:
                        source = local_path or clip_url
                        if source:
                            with st.spinner(f"Extracting thumbnail…"):
                                thumb_url = get_clip_thumbnail_url(source, project_id, fname)
                            st.session_state[thumb_key] = thumb_url or ""
                        else:
                            st.session_state[thumb_key] = ""
                    cached_thumb = st.session_state.get(thumb_key, "")
                    if cached_thumb:
                        try:
                            st.image(cached_thumb, use_container_width=True)
                        except Exception:
                            pass

                # Inline video player
                video_src = local_path or clip_url
                if video_src:
                    try:
                        st.video(video_src)
                    except Exception:
                        st.caption(f"Preview unavailable")

                st.markdown(f"**{fname}**")

                # Duration (local only; fast)
                if local_path and Path(local_path).exists():
                    try:
                        dur = float(get_media_duration(local_path))
                        st.caption(f"Duration: {dur:.1f}s")
                    except Exception:
                        pass

                # Effects badges
                badges = _clip_effects_badges(fname, global_cfg, scene_cfgs)
                if badges:
                    st.caption("Effects: " + " · ".join(badges))

    st.markdown("### Scene assignments")

    clip_options = ["None assigned"] + [c["filename"] for c in all_clips]
    clip_by_name = {c["filename"]: c for c in all_clips}

    def _find_assigned_clip_name(scene_num: int) -> str:
        """Return the clip filename currently assigned to this scene, or 'None assigned'.

        Checks merged_assignments which includes both Supabase and session-local
        assignments so the selectbox reflects assignments even when Supabase save failed.
        """
        info = merged_assignments.get(scene_num)
        if not info:
            return "None assigned"
        stored_url = info.get("url", "")
        stored_fname = info.get("filename", "")
        # Direct filename match (set when stored in session state)
        if stored_fname and stored_fname in [c["filename"] for c in all_clips]:
            return stored_fname
        if not stored_url:
            return "None assigned"
        for c in all_clips:
            if c.get("url") and c["url"] == stored_url:
                return c["filename"]
            # fuzzy match by filename in URL (handles URL variations)
            if c["filename"] in stored_url:
                return c["filename"]
        return "None assigned"

    for idx, scene in enumerate(scenes):
        scene_num = idx + 1
        scene_label = getattr(scene, "title", None) or f"Scene {scene_num}"
        raw_excerpt = str(getattr(scene, "script_excerpt", "") or "")
        excerpt_display = raw_excerpt[:90] + ("…" if len(raw_excerpt) > 90 else "")

        current_clip_name = _find_assigned_clip_name(scene_num)
        default_idx = clip_options.index(current_clip_name) if current_clip_name in clip_options else 0

        with st.container():
            row_cols = st.columns([3, 4, 1])
            with row_cols[0]:
                st.markdown(f"**Scene {scene_num}:** {scene_label}")
                if excerpt_display:
                    st.caption(excerpt_display)
            with row_cols[1]:
                picked = st.selectbox(
                    f"Clip for scene {scene_num}",
                    clip_options,
                    index=default_idx,
                    key=f"clip_assign_pick_s{scene_num:02d}",
                    label_visibility="collapsed",
                )
                # Inline confirmation: show thumbnail + duration for picked clip
                if picked != "None assigned":
                    chosen = clip_by_name.get(picked, {})
                    preview_src = chosen.get("local_path") or chosen.get("url")
                    if preview_src:
                        try:
                            st.video(preview_src)
                        except Exception:
                            pass
                    lp = chosen.get("local_path")
                    if lp and Path(lp).exists():
                        try:
                            dur = float(get_media_duration(lp))
                            st.caption(f"✓ {picked} · {dur:.1f}s")
                        except Exception:
                            st.caption(f"✓ {picked}")

            with row_cols[2]:
                if st.button("Assign", key=f"clip_assign_btn_s{scene_num:02d}", use_container_width=True):
                    if picked == "None assigned":
                        if _sb.is_configured():
                            _sb.remove_clip_assignment(project_id, scene_num)
                        # Also remove from session state
                        sess_del = dict(st.session_state.get("local_clip_assignments", {}))
                        sess_del.pop(scene_num, None)
                        st.session_state["local_clip_assignments"] = sess_del
                        _cached_clip_assignments.clear()
                        st.toast(f"Removed assignment for Scene {scene_num}.")
                    else:
                        chosen = clip_by_name.get(picked, {})
                        clip_url_to_save = str(chosen.get("url") or "")
                        local_path_str = str(chosen.get("local_path") or "")
                        storage_path = str(chosen.get("storage_path") or "")

                        # If no Supabase URL yet but clip exists locally, try uploading it now.
                        if not clip_url_to_save and local_path_str and _sb.is_configured():
                            local_path_obj = Path(local_path_str)
                            if local_path_obj.exists():
                                with st.spinner("Uploading clip to Supabase…"):
                                    try:
                                        storage_path = f"{project_id}/effects_clips/{local_path_obj.name}"
                                        clip_bytes = local_path_obj.read_bytes()
                                        uploaded_url = _sb._upload_bytes(
                                            "history-forge-videos",
                                            storage_path,
                                            clip_bytes,
                                            "video/mp4",
                                        )
                                        if uploaded_url:
                                            clip_url_to_save = str(uploaded_url)
                                            _sb.record_asset(
                                                project_id, "effects_clip",
                                                local_path_obj.name, clip_url_to_save,
                                            )
                                            _cached_effects_clips.clear()
                                    except Exception as _upload_exc:
                                        log.warning(
                                            "[effects_tab] On-the-fly upload failed for %s: %s",
                                            local_path_str, _upload_exc,
                                        )

                        # Use Supabase URL if available; fall back to local path for session storage.
                        effective_url = clip_url_to_save or local_path_str

                        if not effective_url:
                            st.error("No URL or local file found for this clip. Render clips first.")
                        elif _sb.is_configured():
                            ok = _sb.save_clip_assignment(
                                project_id, scene_num, storage_path, effective_url
                            )
                            _cached_clip_assignments.clear()
                            if ok:
                                st.toast(f"✓ '{picked}' assigned to Scene {scene_num}.")
                            else:
                                # Supabase save failed — persist in session state so the
                                # assignment is at least usable for the rest of this session.
                                sess_assignments = dict(st.session_state.get("local_clip_assignments", {}))
                                sess_assignments[scene_num] = {
                                    "url": effective_url,
                                    "filename": picked,
                                }
                                st.session_state["local_clip_assignments"] = sess_assignments
                                st.warning(
                                    "Supabase save failed — assignment stored in session only "
                                    "(will not persist after page refresh). "
                                    "Check Supabase Diagnostics for details."
                                )
                        else:
                            # Supabase not configured; store in session state only
                            sess_assignments = dict(st.session_state.get("local_clip_assignments", {}))
                            sess_assignments[scene_num] = {
                                "url": effective_url,
                                "filename": picked,
                            }
                            st.session_state["local_clip_assignments"] = sess_assignments
                            st.toast(f"✓ '{picked}' assigned to Scene {scene_num} (session only — Supabase not configured).")
                    st.rerun()

        st.divider()


# ── Main tab entry point ───────────────────────────────────────────────────────

def tab_video_effects() -> None:
    """Render the 🎬 Video Effects tab."""
    st.header("🎬 Video Effects")
    st.caption(
        "Configure cinematic effects (Ken Burns, colour grade, film grain, …) "
        "for each scene image.  Processed clips are saved locally and uploaded to "
        "Supabase, then used by the Video Studio as pre-rendered inputs."
    )

    project_id = active_project_id()
    scenes = st.session_state.get("scenes", [])

    if not scenes:
        st.info(
            "No scenes found for this project.  Go to the **🧩 Scenes** tab to "
            "define scenes, then generate images in the **🖼️ Images** tab."
        )
        return

    # ── Load saved configs ────────────────────────────────────────────────────
    global_cfg: GlobalEffectsConfig = load_global_config(project_id)
    scene_cfgs: list[SceneEffectsConfig] = load_scene_configs(project_id, len(scenes))

    # ── Output resolution selector ────────────────────────────────────────────
    with st.expander("Output resolution & FPS", expanded=False):
        res_col, fps_col = st.columns(2)
        with res_col:
            resolution = st.selectbox(
                "Resolution",
                ["1920×1080 (16:9 landscape)", "1080×1920 (9:16 portrait)"],
                index=0 if global_cfg.output_width == 1920 else 1,
                key="fx_resolution",
            )
            if "landscape" in resolution:
                global_cfg.output_width, global_cfg.output_height = 1920, 1080
            else:
                global_cfg.output_width, global_cfg.output_height = 1080, 1920
        with fps_col:
            global_cfg.output_fps = st.selectbox(
                "FPS", [24, 25, 30], index=[24, 25, 30].index(global_cfg.output_fps), key="fx_fps"
            )

    # ── Global Defaults ───────────────────────────────────────────────────────
    global_cfg = _render_global_defaults(global_cfg)

    st.divider()

    # ── Per-Scene Overrides ───────────────────────────────────────────────────
    st.subheader("Per-Scene Overrides")
    st.caption("Expand a scene to customise its effects.  Leave 'Use global default' checked to inherit global settings.")

    images_dir = PROJECTS_ROOT / slugify_project_id(project_id) / "assets" / "images"
    updated_scene_cfgs: list[SceneEffectsConfig] = []
    for idx, scene in enumerate(scenes):
        scene_num = idx + 1
        label = getattr(scene, "title", None) or f"Scene {scene_num}"
        img = images_dir / f"s{scene_num:02d}.png"
        scene_cfg = scene_cfgs[idx] if idx < len(scene_cfgs) else SceneEffectsConfig()
        updated = _render_scene_override(scene_num, label, img if img.exists() else None, scene_cfg, global_cfg)
        updated_scene_cfgs.append(updated)

    st.divider()

    # ── Save config button ────────────────────────────────────────────────────
    save_col, apply_col, render_col = st.columns([1, 1.3, 1.7])
    with save_col:
        if st.button("💾 Save config", use_container_width=True):
            ok_g = save_global_config(global_cfg, project_id)
            ok_s = save_scene_configs(updated_scene_cfgs, project_id)
            if ok_g or ok_s:
                st.success("Effects config saved.")
            else:
                st.error("Could not save config (check logs).")

    with apply_col:
        apply_to_scene_editor_btn = st.button(
            "🎞️ Apply rendered clips to Scene Editor",
            use_container_width=True,
            help="Copies s01/s02/... effects clips into canonical scene video slots so each scene shows its matching video in the Scene Editor.",
        )

    with render_col:
        render_btn = st.button(
            "🎬 Apply Effects & Render Clips",
            type="primary",
            use_container_width=True,
            help=(
                "Renders each scene image through the effects chain, saves clips "
                "locally under assets/effects_clips/, uploads to Supabase, and "
                "marks scenes as ready for final assembly."
            ),
        )

    if apply_to_scene_editor_btn:
        clips_dir = _effects_clips_dir()
        assigned, missing = _apply_effects_clips_to_scene_editor(
            scenes=scenes,
            project_id=project_id,
            clips_dir=clips_dir,
        )
        if assigned == 0:
            st.warning("No rendered effects clips found to apply. Render clips first.")
        elif missing == 0:
            st.success(f"Applied {assigned} rendered clip(s) to their matching scenes.")
        else:
            st.warning(
                f"Applied {assigned} clip(s). {missing} scene(s) had no rendered effects clip and were left unchanged."
            )

    if render_btn:
        # Auto-save config first.
        save_global_config(global_cfg, project_id)
        save_scene_configs(updated_scene_cfgs, project_id)

        clips_dir = _effects_clips_dir()
        st.markdown("---")
        st.subheader("Rendering…")

        rendered_clips = _apply_and_upload_clips(
            scenes=scenes,
            project_id=project_id,
            global_cfg=global_cfg,
            scene_cfgs=updated_scene_cfgs,
            clips_dir=clips_dir,
        )

        # Bust the effects-clip cache so the Assign section below
        # immediately shows the freshly-uploaded clips with their URLs.
        _cached_effects_clips.clear()
        _cached_clip_assignments.clear()

        # ── Summary ──────────────────────────────────────────────────────────
        ok_count = sum(1 for c in rendered_clips if c is not None)
        fail_count = len(rendered_clips) - ok_count

        if ok_count == len(rendered_clips):
            st.success(f"All {ok_count} scene clips rendered successfully!")
        elif ok_count > 0:
            st.warning(f"{ok_count} clips rendered; {fail_count} failed (originals will be used).")
        else:
            st.error("All clips failed.  Check FFmpeg is installed and images exist.")

        # ── Store clip paths in session state so Video Studio can use them ────
        # Map scene index → local clip path (str) for scenes that succeeded.
        effects_clip_paths: dict[int, str] = {}
        for i, clip_path in enumerate(rendered_clips):
            if clip_path is not None:
                effects_clip_paths[i + 1] = str(clip_path)
        st.session_state["effects_clip_paths"] = effects_clip_paths

        if ok_count > 0:
            st.info(
                "Clips are ready.  Go to the **🎬 Video Studio** tab and render your final video "
                "— the effects clips will be picked up automatically as pre-rendered scene inputs."
            )

    # ── Preview: already-rendered clips ──────────────────────────────────────
    clips_dir = _effects_clips_dir()
    existing_clips = sorted(clips_dir.glob("s??_effects.mp4"))
    if existing_clips:
        st.divider()
        st.subheader("Rendered Clips Preview")
        cols = st.columns(min(3, len(existing_clips)))
        for i, clip in enumerate(existing_clips):
            with cols[i % 3]:
                st.caption(clip.stem)
                st.video(str(clip))

    # ── Assign Clips to Scenes ────────────────────────────────────────────────
    _render_assign_clips_section(
        scenes=scenes,
        project_id=project_id,
        global_cfg=global_cfg,
        scene_cfgs=updated_scene_cfgs,
        clips_dir=clips_dir,
    )
