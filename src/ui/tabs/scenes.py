from pathlib import Path
import json

import streamlit as st

from utils import Scene, split_script_into_scenes

from src.ui.state import active_project_id, clear_downstream, scenes_ready, script_ready
from src.ui.timeline_sync import sync_timeline_for_project


def _project_path() -> Path:
    return Path("data/projects") / active_project_id()


def _timeline_state_key() -> str:
    return f"video_scene_captions::{_project_path() / 'timeline.json'}"


def _sync_timeline_from_scenes() -> None:
    sync_timeline_for_project(
        project_path=_project_path(),
        project_id=active_project_id(),
        title=st.session_state.project_title,
        session_scenes=st.session_state.get("scenes", []),
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

    caption_state_key = _timeline_state_key()
    captions = st.session_state.get(caption_state_key)
    if isinstance(captions, list):
        reordered: list[str] = [""] * len(scenes)
        for old_index, new_index in index_map.items():
            old_pos = old_index - 1
            new_pos = new_index - 1
            if 0 <= old_pos < len(captions):
                reordered[new_pos] = captions[old_pos]
        st.session_state[caption_state_key] = reordered

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


def tab_create_scenes() -> None:
    st.subheader("Create scenes")

    if not script_ready():
        st.warning("Paste or generate a script first.")
        return

    st.session_state.max_scenes = st.number_input(
        "Number of scenes",
        min_value=3,
        max_value=75,
        value=int(st.session_state.max_scenes),
        step=1,
    )
    st.session_state.scene_wpm = st.number_input(
        "Narration speed (WPM)",
        min_value=90,
        max_value=240,
        value=int(st.session_state.scene_wpm),
        step=5,
        help="Used to estimate each scene's duration and total runtime.",
    )

    if st.button("Split script into scenes", type="primary", width="stretch"):
        with st.spinner("Splitting script..."):
            st.session_state.scenes = split_script_into_scenes(
                st.session_state.script_text,
                max_scenes=int(st.session_state.max_scenes),
                outline=_outline_payload(),
                wpm=int(st.session_state.scene_wpm),
            )
        clear_downstream("scenes")
        st.session_state.storyboard_selected_pos = 0
        _recompute_estimated_runtime()
        _sync_timeline_from_scenes()
        st.toast(f"Created {len(st.session_state.scenes)} scenes.")
        st.rerun()

    if not scenes_ready():
        st.info("No scenes yet.")
        return

    scenes: list[Scene] = st.session_state.scenes
    _recompute_estimated_runtime()
    st.caption(f"Estimated runtime: {_fmt_runtime(float(st.session_state.get('estimated_total_runtime_sec', 0.0)))}")
    st.session_state.setdefault("storyboard_selected_pos", 0)
    st.session_state.storyboard_selected_pos = max(
        0,
        min(int(st.session_state.storyboard_selected_pos), len(scenes) - 1),
    )

    left, center, right = st.columns([1.2, 2, 2])

    with left:
        st.markdown("### Storyboard")
        for pos, scene in enumerate(scenes):
            row = st.columns([1, 1, 3])
            with row[0]:
                if st.button("↑", key=f"scene_up_{pos}_{scene.index}", disabled=pos == 0, width="stretch"):
                    _move_scene(pos, -1)
            with row[1]:
                if st.button("↓", key=f"scene_down_{pos}_{scene.index}", disabled=pos == len(scenes) - 1, width="stretch"):
                    _move_scene(pos, 1)
            with row[2]:
                is_selected = pos == st.session_state.storyboard_selected_pos
                label = f"{scene.index:02d} — {scene.title}"
                if st.button(("✅ " if is_selected else "") + label, key=f"scene_pick_{pos}_{scene.index}", width="stretch"):
                    st.session_state.storyboard_selected_pos = pos
                    st.rerun()

    selected = scenes[st.session_state.storyboard_selected_pos]

    with center:
        st.markdown("### Scene editor")
        selected.title = st.text_input("Title", value=selected.title, key=f"story_title_{selected.index}")
        selected.script_excerpt = st.text_area(
            "Excerpt",
            value=selected.script_excerpt,
            height=200,
            key=f"story_excerpt_{selected.index}",
        )
        selected.visual_intent = st.text_area(
            "Visual intent",
            value=selected.visual_intent,
            height=140,
            key=f"story_visual_{selected.index}",
        )
        est_sec = float(getattr(selected, "estimated_duration_sec", 0.0) or 0.0)
        selected.estimated_duration_sec = float(
            st.number_input(
                "Scene duration (seconds)",
                min_value=0.5,
                max_value=60.0,
                value=max(0.5, est_sec if est_sec > 0 else 3.0),
                step=0.1,
                key=f"story_duration_{selected.index}",
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
            key=f"story_prompt_{selected.index}",
        )
        if selected.image_bytes:
            st.image(selected.image_bytes, width="stretch")
        else:
            saved = _project_path() / "assets/images" / f"s{selected.index:02d}.png"
            if saved.exists():
                st.image(str(saved), width="stretch")
            else:
                st.caption("No image selected yet.")

        caption_state_key = _timeline_state_key()
        captions = st.session_state.get(caption_state_key, [])
        if len(captions) != len(scenes):
            captions = [str(scene.script_excerpt or "") for scene in scenes]
        caption_value = captions[selected.index - 1] if selected.index - 1 < len(captions) else ""
        edited_caption = st.text_area(
            "Caption",
            value=caption_value or str(selected.script_excerpt or ""),
            height=120,
            key=f"story_caption_{selected.index}",
            help="Used in timeline.json; defaults to script excerpt.",
        )
        if selected.index - 1 < len(captions):
            captions[selected.index - 1] = edited_caption
        st.session_state[caption_state_key] = captions

    if st.button("Apply storyboard changes", type="primary", width="stretch"):
        _recompute_estimated_runtime()
        sync_timeline_for_project(
            project_path=_project_path(),
            project_id=active_project_id(),
            title=st.session_state.project_title,
            session_scenes=scenes,
            scene_captions=st.session_state.get(_timeline_state_key()),
        )
        st.toast("Storyboard updates saved.")
        st.rerun()

    with st.expander("Advanced: bulk edit", expanded=False):
        pending_edits: dict[int, dict[str, str]] = {}
        for s in scenes:
            st.markdown(f"#### {s.index:02d} — {s.title}")
            st.text_input("Title", value=s.title, key=f"bulk_title_{s.index}")
            st.text_area("Excerpt", value=s.script_excerpt, height=120, key=f"bulk_txt_{s.index}")
            st.text_area("Visual intent", value=s.visual_intent, height=90, key=f"bulk_vi_{s.index}")
            pending_edits[s.index] = {
                "title": st.session_state.get(f"bulk_title_{s.index}", s.title),
                "script_excerpt": st.session_state.get(f"bulk_txt_{s.index}", s.script_excerpt),
                "visual_intent": st.session_state.get(f"bulk_vi_{s.index}", s.visual_intent),
            }

        if st.button("Save bulk edits", width="stretch"):
            for s in scenes:
                edits = pending_edits.get(s.index, {})
                s.title = edits.get("title", s.title)
                s.script_excerpt = edits.get("script_excerpt", s.script_excerpt)
                s.visual_intent = edits.get("visual_intent", s.visual_intent)
            _recompute_estimated_runtime()
            _sync_timeline_from_scenes()
            st.toast("Bulk edits saved.")
            st.rerun()
