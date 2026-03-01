from pathlib import Path
import json

import streamlit as st

from utils import Scene, split_script_into_scenes

from src.ui.state import active_project_id, clear_downstream, scenes_ready, script_ready
from src.ui.timeline_sync import sync_timeline_for_project
from src.video.utils import get_media_duration


def _saved_videos_for_project(project_id: str) -> list[Path]:
    """Return locally saved AI-generated .mp4 files for *project_id*, newest first."""
    d = Path("data/projects") / project_id / "assets/videos"
    if not d.exists():
        return []
    return sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)


_TRANSITION_OPTIONS = [
    "fade",
    "fadeblack",
    "fadewhite",
    "wipeleft",
    "wiperight",
    "slideleft",
    "slideright",
    "smoothleft",
    "smoothright",
]


def _project_path() -> Path:
    return Path("data/projects") / active_project_id()


def _remove_scene_image_asset(scene: Scene) -> None:
    """Clear in-memory and on-disk image assets when a video is selected."""
    scene.image_bytes = None
    scene.image_variations = []
    image_path = _project_path() / "assets/images" / f"s{scene.index:02d}.png"
    if image_path.exists():
        image_path.unlink(missing_ok=True)


def _timeline_state_key() -> str:
    return f"video_scene_captions::{_project_path() / 'timeline.json'}"


def _captions_from_scenes(scenes: list[Scene]) -> list[str]:
    return [str(scene.script_excerpt or "") for scene in scenes]


def _scene_widget_key(prefix: str, scene: Scene) -> str:
    return f"{prefix}{scene.index}_{getattr(scene, 'scene_id', '')}"


def _normalize_scene_transitions(scene_count: int) -> list[str]:
    needed = max(0, scene_count - 1)
    current = st.session_state.get("scene_transition_types", [])
    transitions = [str(item or "fade") for item in current] if isinstance(current, list) else []
    transitions = [item if item in _TRANSITION_OPTIONS else "fade" for item in transitions[:needed]]
    if len(transitions) < needed:
        transitions.extend(["fade"] * (needed - len(transitions)))
    st.session_state.scene_transition_types = transitions
    return transitions


def _sync_timeline_from_scenes() -> None:
    scenes = st.session_state.get("scenes", [])
    transitions = _normalize_scene_transitions(len(scenes) if isinstance(scenes, list) else 0)
    sync_timeline_for_project(
        project_path=_project_path(),
        project_id=active_project_id(),
        title=st.session_state.project_title,
        session_scenes=scenes,
        meta_overrides={"transition_types": transitions},
    )


def _outline_payload() -> dict[str, object] | None:
    raw = str(st.session_state.get("outline_json_text", "") or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _primary_voiceover_path() -> Path | None:
    audio_dir = _project_path() / "assets/audio"
    preferred = audio_dir / "voiceover.mp3"
    if preferred.exists():
        return preferred
    candidates = sorted([p for p in audio_dir.glob("*.*") if p.suffix.lower() in {".mp3", ".wav"}])
    return candidates[0] if candidates else None


def _equal_scene_durations(scene_count: int, total_duration: float) -> list[float]:
    if scene_count <= 0 or total_duration <= 0:
        return []
    even = float(total_duration) / float(scene_count)
    durations = [even] * scene_count
    correction = float(total_duration) - sum(durations)
    if durations:
        durations[-1] += correction
    return durations


def _auto_match_scene_lengths_to_voiceover_equal(scenes: list[Scene]) -> tuple[bool, str]:
    voiceover_path = _primary_voiceover_path()
    if voiceover_path is None:
        return False, "No voiceover file found in assets/audio. Generate or add voiceover first."

    try:
        voiceover_duration = float(get_media_duration(voiceover_path))
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not read voiceover duration: {exc}"

    durations = _equal_scene_durations(len(scenes), voiceover_duration)
    if not durations:
        return False, "No scenes available to adjust."

    for idx, (scene, duration) in enumerate(zip(scenes, durations), start=1):
        scene.estimated_duration_sec = float(duration)
        st.session_state[_scene_widget_key("story_duration_", scene)] = float(duration)

    st.session_state[_timeline_state_key()] = _captions_from_scenes(scenes)
    _recompute_estimated_runtime()
    _sync_timeline_from_scenes()

    return True, f"Updated {len(scenes)} scene(s) to evenly match voiceover length ({_fmt_runtime(voiceover_duration)})."


def _recompute_estimated_runtime() -> None:
    total = 0.0
    for scene in st.session_state.get("scenes", []):
        try:
            total += float(getattr(scene, "estimated_duration_sec", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    st.session_state.estimated_total_runtime_sec = round(total, 1)


def _fmt_runtime(seconds: float) -> str:
    total = int(max(0, round(seconds)))
    mins = total // 60
    secs = total % 60
    return f"{mins}m {secs:02d}s"


def _remap_scene_widget_state(index_map: dict[int, int]) -> None:
    key_prefixes = ["title_", "txt_", "vi_", "prompt_", "scene_upload_", "regen_", "story_title_", "story_excerpt_", "story_visual_", "story_prompt_", "story_caption_", "story_duration_"]
    for prefix in key_prefixes:
        remapped: dict[str, object] = {}
        to_delete: list[str] = []
        for old_index, new_index in index_map.items():
            old_key = f"{prefix}{old_index}"
            if old_key in st.session_state:
                remapped[f"{prefix}{new_index}"] = st.session_state[old_key]
                to_delete.append(old_key)
        for key in to_delete:
            del st.session_state[key]
        for key, value in remapped.items():
            st.session_state[key] = value


def _rename_scene_assets(index_map: dict[int, int]) -> None:
    images_dir = _project_path() / "assets/images"
    if not images_dir.exists():
        return

    planned: list[tuple[Path, Path]] = []
    for old_index, new_index in index_map.items():
        src = images_dir / f"s{old_index:02d}.png"
        dst = images_dir / f"s{new_index:02d}.png"
        if src.exists() and src != dst:
            planned.append((src, dst))

    temp_moves: list[tuple[Path, Path]] = []
    for src, dst in planned:
        tmp = src.with_name(f"{src.stem}__tmp_reindex{src.suffix}")
        src.rename(tmp)
        temp_moves.append((tmp, dst))

    for tmp, dst in temp_moves:
        if dst.exists():
            dst.unlink()
        tmp.rename(dst)


def _reindex_scenes_and_assets() -> None:
    scenes: list[Scene] = st.session_state.scenes
    index_map: dict[int, int] = {}
    for new_index, scene in enumerate(scenes, start=1):
        index_map[scene.index] = new_index
        scene.index = new_index

    _remap_scene_widget_state(index_map)
    _rename_scene_assets(index_map)

    st.session_state[_timeline_state_key()] = _captions_from_scenes(scenes)
    _sync_timeline_from_scenes()


def _move_scene(scene_position: int, direction: int) -> None:
    scenes: list[Scene] = st.session_state.scenes
    target = scene_position + direction
    if target < 0 or target >= len(scenes):
        return
    scenes[scene_position], scenes[target] = scenes[target], scenes[scene_position]
    st.session_state.storyboard_selected_pos = target
    _reindex_scenes_and_assets()
    st.rerun()


def _reset_all_scenes() -> None:
    st.session_state.scenes = []
    st.session_state.scene_transition_types = []
    st.session_state.storyboard_selected_pos = 0
    st.session_state.estimated_total_runtime_sec = 0.0
    st.session_state[_timeline_state_key()] = []

def tab_create_scenes() -> None:
    st.subheader("Create scenes")

    if not script_ready():
        st.warning("Paste or generate a script first.")
        return

    st.number_input(
        "Number of scenes",
        min_value=3,
        max_value=75,
        step=1,
        key="max_scenes",
    )
    st.number_input(
        "Narration speed (WPM)",
        min_value=90,
        max_value=240,
        step=5,
        key="scene_wpm",
        help="Used to estimate each scene's duration and total runtime.",
    )

    create_col, reset_col = st.columns([3, 2])
    with create_col:
        create_scenes_clicked = st.button("Split script into scenes", type="primary", width="stretch")
    with reset_col:
        reset_scenes_clicked = st.button(
            "Reset scenes",
            width="stretch",
            disabled=not scenes_ready(),
            help="Clear generated scenes and scene transitions, while keeping your script.",
        )

    if create_scenes_clicked:
        script_for_splitter = (
            str(st.session_state.get("generated_script_text_input", "") or "").strip()
            or str(st.session_state.get("script_text_input", "") or "").strip()
            or str(st.session_state.get("script_text", "") or "").strip()
        )
        st.write("DEBUG max_scenes:", st.session_state.max_scenes)
        st.write("DEBUG script length:", len(script_for_splitter))
        with st.spinner("Splitting script..."):
            st.session_state.scenes = split_script_into_scenes(
                script_for_splitter,
                max_scenes=int(st.session_state.max_scenes),
                outline=_outline_payload(),
                wpm=int(st.session_state.scene_wpm),
            )
        clear_downstream("scenes")
        st.session_state.scene_transition_types = ["fade"] * max(0, len(st.session_state.scenes) - 1)
        st.session_state.storyboard_selected_pos = 0
        _recompute_estimated_runtime()
        _sync_timeline_from_scenes()
        st.toast(f"Created {len(st.session_state.scenes)} scenes.")
        st.caption(f"Split debug: {len(st.session_state.scenes)} scene(s)")
        for debug_scene in st.session_state.scenes:
            st.write(debug_scene.index, debug_scene.title, str(debug_scene.script_excerpt or "")[:60])
        st.rerun()

    if reset_scenes_clicked:
        _reset_all_scenes()
        clear_downstream("scenes")
        st.toast("Scenes reset.")
        st.rerun()

    if not scenes_ready():
        st.info("No scenes yet.")
        return

    scenes: list[Scene] = st.session_state.scenes
    transitions = _normalize_scene_transitions(len(scenes))
    _recompute_estimated_runtime()
    st.caption(f"Estimated runtime: {_fmt_runtime(float(st.session_state.get('estimated_total_runtime_sec', 0.0)))}")

    if st.button("Auto-match scene lengths to voiceover (equal)", width="stretch", key="scene_auto_match_vo_equal"):
        ok, message = _auto_match_scene_lengths_to_voiceover_equal(scenes)
        if ok:
            st.success(message)
            st.rerun()
        st.warning(message)
    st.session_state.setdefault("storyboard_selected_pos", 0)
    st.session_state.storyboard_selected_pos = max(
        0,
        min(int(st.session_state.storyboard_selected_pos), len(scenes) - 1),
    )

    st.markdown("### Transitions between scenes")
    transitions = _normalize_scene_transitions(len(scenes))
    if not transitions:
        st.caption("At least 2 scenes are required for transitions.")
    else:
        preset_cols = st.columns([2, 1])
        with preset_cols[0]:
            transition_preset = st.selectbox(
                "Apply one transition style to all boundaries",
                _TRANSITION_OPTIONS,
                index=0,
                key="scene_transition_preset",
            )
        with preset_cols[1]:
            if st.button("Apply to all", width="stretch", key="scene_transition_apply_all"):
                transitions = [transition_preset] * len(transitions)
                st.session_state.scene_transition_types = transitions
                st.rerun()

        for i in range(len(transitions)):
            left_title = scenes[i].title or f"Scene {i+1}"
            right_title = scenes[i + 1].title or f"Scene {i+2}"
            transitions[i] = st.selectbox(
                f"{i+1:02d} — {left_title} → {right_title}",
                _TRANSITION_OPTIONS,
                index=_TRANSITION_OPTIONS.index(transitions[i]) if transitions[i] in _TRANSITION_OPTIONS else 0,
                key=f"scene_transition_{i+1}",
            )
        st.session_state.scene_transition_types = transitions

    left, center, right = st.columns([1.2, 2, 2])

    with left:
        st.markdown("### Storyboard")
        for pos, scene in enumerate(scenes):
            row = st.columns([1, 1, 3])
            with row[0]:
                if st.button("↑", key=_scene_widget_key(f"scene_up_{pos}_", scene), disabled=pos == 0, width="stretch"):
                    _move_scene(pos, -1)
            with row[1]:
                if st.button("↓", key=_scene_widget_key(f"scene_down_{pos}_", scene), disabled=pos == len(scenes) - 1, width="stretch"):
                    _move_scene(pos, 1)
            with row[2]:
                is_selected = pos == st.session_state.storyboard_selected_pos
                label = f"{scene.index:02d} — {scene.title}"
                if st.button(("✅ " if is_selected else "") + label, key=_scene_widget_key(f"scene_pick_{pos}_", scene), width="stretch"):
                    st.session_state.storyboard_selected_pos = pos
                    st.rerun()

    selected = scenes[st.session_state.storyboard_selected_pos]

    with center:
        st.markdown("### Scene editor")
        selected.title = st.text_input("Title", value=selected.title, key=_scene_widget_key("story_title_", selected))
        selected.script_excerpt = st.text_area(
            "Excerpt",
            value=selected.script_excerpt,
            height=200,
            key=_scene_widget_key("story_excerpt_", selected),
        )
        selected.visual_intent = st.text_area(
            "Visual intent",
            value=selected.visual_intent,
            height=140,
            key=_scene_widget_key("story_visual_", selected),
        )
        est_sec = float(getattr(selected, "estimated_duration_sec", 0.0) or 0.0)
        _dur_min = 0.5
        _dur_max = 300.0
        _dur_val = max(_dur_min, min(_dur_max, est_sec if est_sec > 0 else 3.0))
        selected.estimated_duration_sec = float(
            st.number_input(
                "Scene duration (seconds)",
                min_value=_dur_min,
                max_value=_dur_max,
                value=_dur_val,
                step=0.1,
                key=_scene_widget_key("story_duration_", selected),
                help="Initial values are auto-estimated from script pace; adjust per scene as needed.",
            )
        )
        st.caption(f"Estimated duration: {_fmt_runtime(float(selected.estimated_duration_sec))}")

    with right:
        st.markdown("### Prompt + media")
        selected.image_prompt = st.text_area(
            "Prompt",
            value=selected.image_prompt or "",
            height=140,
            key=_scene_widget_key("story_prompt_", selected),
        )
        if selected.image_bytes:
            st.image(selected.image_bytes, width="stretch")
        else:
            saved = _project_path() / "assets/images" / f"s{selected.index:02d}.png"
            if saved.exists():
                st.image(str(saved), width="stretch")
            else:
                st.caption("No image selected yet.")

        # ------------------------------------------------------------------
        # AI video clip assigned to this scene
        # ------------------------------------------------------------------
        st.markdown("#### AI video clip")
        scene_video_path = getattr(selected, "video_path", None)
        scene_video_url = getattr(selected, "video_url", None)

        # Resolve display source: prefer a valid local file, fall back to URL
        _video_src = None
        if scene_video_path and Path(scene_video_path).exists():
            _video_src = scene_video_path
        elif scene_video_url and str(scene_video_url).startswith(("http://", "https://")):
            _video_src = scene_video_url

        if _video_src:
            st.video(_video_src)
            selected.video_loop = bool(
                st.checkbox(
                    "Loop video to fill scene duration",
                    value=bool(getattr(selected, "video_loop", False)),
                    key=_scene_widget_key("scene_video_loop_", selected),
                )
            )
            selected.video_muted = bool(
                st.checkbox(
                    "Mute video audio",
                    value=bool(getattr(selected, "video_muted", True)),
                    key=_scene_widget_key("scene_video_muted_", selected),
                    help="Default is muted so narration/music remain clear.",
                )
            )
            selected.video_volume = float(
                st.slider(
                    "Video audio volume",
                    min_value=0,
                    max_value=100,
                    value=int(max(0.0, min(100.0, float(getattr(selected, "video_volume", 0.0) or 0.0)))),
                    key=_scene_widget_key("scene_video_volume_", selected),
                    disabled=bool(getattr(selected, "video_muted", True)),
                )
            )
            if scene_video_path:
                st.caption(f"`{Path(scene_video_path).name}`")
            if st.button(
                "Remove video",
                key=_scene_widget_key("scene_remove_video_", selected),
                help="Unlink the video from this scene (the file is not deleted).",
            ):
                selected.video_path = None
                selected.video_url = None
                st.rerun()
        else:
            st.caption("No AI video assigned to this scene.")

        # Picker: load from saved project videos
        project_id = active_project_id()
        saved_vids = _saved_videos_for_project(project_id)
        if saved_vids:
            vid_names = [v.name for v in saved_vids]
            pick_key = _scene_widget_key("scene_vid_pick_", selected)
            assign_key = _scene_widget_key("scene_vid_assign_", selected)
            picked = st.selectbox(
                "Load saved video",
                ["— choose —"] + vid_names,
                key=pick_key,
                help="Select a previously generated video to assign to this scene.",
            )
            if st.button("Assign video", key=assign_key, disabled=(picked == "— choose —")):
                chosen_path = saved_vids[vid_names.index(picked)]
                selected.video_path = str(chosen_path)
                selected.video_url = None
                selected.video_loop = bool(getattr(selected, "video_loop", False))
                selected.video_muted = True
                selected.video_volume = 0.0
                _remove_scene_image_asset(selected)
                st.toast(f"Video '{picked}' assigned to scene {selected.index}.")
                st.rerun()
        else:
            st.caption("Use the **AI Video Generator** tab to create videos for this project.")

        caption_state_key = _timeline_state_key()
        captions = _captions_from_scenes(scenes)
        st.session_state[caption_state_key] = captions
        caption_value = captions[selected.index - 1] if selected.index - 1 < len(captions) else ""
        st.text_area(
            "Caption (matches excerpt)",
            value=caption_value,
            height=120,
            key=_scene_widget_key("story_caption_", selected),
            help="Captions are synced from each scene excerpt to keep preview/video text aligned.",
            disabled=True,
        )

    if st.button("Apply storyboard changes", type="primary", width="stretch"):
        _recompute_estimated_runtime()
        sync_timeline_for_project(
            project_path=_project_path(),
            project_id=active_project_id(),
            title=st.session_state.project_title,
            session_scenes=scenes,
            scene_captions=_captions_from_scenes(scenes),
            meta_overrides={"transition_types": _normalize_scene_transitions(len(scenes))},
        )
        st.toast("Storyboard updates saved.")
        st.rerun()

    with st.expander("Advanced: bulk edit", expanded=False):
        pending_edits: dict[int, dict[str, str]] = {}
        for s in scenes:
            st.markdown(f"#### {s.index:02d} — {s.title}")
            st.text_input("Title", value=s.title, key=_scene_widget_key("bulk_title_", s))
            st.text_area("Excerpt", value=s.script_excerpt, height=120, key=_scene_widget_key("bulk_txt_", s))
            st.text_area("Visual intent", value=s.visual_intent, height=90, key=_scene_widget_key("bulk_vi_", s))
            pending_edits[s.index] = {
                "title": st.session_state.get(_scene_widget_key("bulk_title_", s), s.title),
                "script_excerpt": st.session_state.get(_scene_widget_key("bulk_txt_", s), s.script_excerpt),
                "visual_intent": st.session_state.get(_scene_widget_key("bulk_vi_", s), s.visual_intent),
            }

        if st.button("Save bulk edits", width="stretch"):
            for s in scenes:
                edits = pending_edits.get(s.index, {})
                s.title = edits.get("title", s.title)
                s.script_excerpt = edits.get("script_excerpt", s.script_excerpt)
                s.visual_intent = edits.get("visual_intent", s.visual_intent)
            _recompute_estimated_runtime()
            st.session_state[_timeline_state_key()] = _captions_from_scenes(scenes)
            _sync_timeline_from_scenes()
            st.toast("Bulk edits saved.")
            st.rerun()
