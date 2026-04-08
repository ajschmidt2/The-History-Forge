from __future__ import annotations

import math
import hashlib
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_secret
from src.storage.supabase_assets import stage_timeline_assets

from .audio_mix import build_audio_mix_cmd
from .captions import write_ass_file, write_srt_file
from .timeline_schema import Timeline
from .utils import ensure_ffmpeg_exists, ensure_parent_dir, get_media_duration as _probe_media_duration, resolve_ffmpeg_exe, run_cmd


VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
_ALLOWED_XFADE_TRANSITIONS = {"fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "slideright", "smoothleft", "smoothright", "circleopen", "circleclose", "distance"}
AI_SCENE_CLIP_MAPPING = {
    "s01": "ai_opening_clip_path",
    "s03": "ai_q2_clip_path",
    "s05": "ai_q3_clip_path",
    "s07": "ai_q4_clip_path",
}


def _normalize_xfade_transition(name: str | None) -> str:
    transition = str(name or "fade").strip().lower()
    return transition if transition in _ALLOWED_XFADE_TRANSITIONS else "fade"


def _parse_resolution(resolution: str) -> tuple[int, int]:
    if "x" not in resolution:
        raise ValueError("Resolution must be formatted like 1080x1920")
    width, height = resolution.lower().split("x", maxsplit=1)
    return int(width), int(height)


def _probe_video_dimensions(video_path: Path) -> str:
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(video_path),
        ]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        dims = (result.stdout or "").strip()
        if "x" in dims:
            return dims
    except Exception:
        pass
    return "unknown"


def _apply_max_width(width: int, height: int, max_width: int) -> tuple[int, int]:
    if width <= max_width:
        return width, height
    scale = max_width / float(width)
    scaled_height = int(height * scale)
    if scaled_height % 2:
        scaled_height += 1
    return max_width, max(2, scaled_height)


def _normalize_scene_duration(duration: float, fps: int, scene_id: str) -> float:
    min_duration = 1.0 / max(1, fps)
    if not math.isfinite(duration):
        raise ValueError(f"Scene '{scene_id}' has non-finite duration {duration!r}.")
    if duration <= 0:
        raise ValueError(f"Scene '{scene_id}' has invalid duration {duration}s (must be > 0).")
    return max(duration, min_duration)


def _safe_crossfade_duration(durations: list[float], requested: float, fps: int) -> float:
    if not durations or len(durations) < 2:
        return 0.0
    if not math.isfinite(requested) or requested <= 0:
        return 0.0
    min_frame = 1.0 / max(1, fps)
    shortest_scene = min(durations)
    max_crossfade = max(0.0, shortest_scene - min_frame)
    return min(requested, max_crossfade)


def get_media_duration(path: str | Path) -> float:
    try:
        duration = float(_probe_media_duration(path))
    except Exception:
        return 0.0
    return duration if math.isfinite(duration) and duration >= 0 else 0.0


def ffprobe_duration(path: str | Path) -> float:
    return get_media_duration(path)


def get_scene_target_duration(scene_id: str, scene_duration_lookup: dict[str, float]) -> float:
    return float(scene_duration_lookup.get(scene_id, 0.0))


def get_ai_clip_for_scene(scene_id: str, ai_clip_map: dict[str, str]) -> str | None:
    clip = ai_clip_map.get(scene_id)
    return clip if clip else None


def trim_clip(
    path: Path,
    duration: float,
    output_path: Path,
    ffmpeg_commands: list[list[str]],
    log_path: Path | None,
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-vf",
        f"tpad=stop_mode=clone:stop_duration={max(0.0, duration):.6f}",
        "-t",
        f"{max(0.0, duration):.6f}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    ffmpeg_commands.append(cmd)
    run_cmd(cmd, log_path=log_path, timeout_sec=command_timeout_sec, workdir=workdir, cwd=cwd)
    return output_path


def concat_clips(
    paths: list[Path],
    output_path: Path,
    ffmpeg_commands: list[list[str]],
    log_path: Path | None,
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    if not paths:
        raise ValueError("concat_clips requires at least one input path.")
    cmd = ["ffmpeg", "-y"]
    for clip_path in paths:
        cmd.extend(["-i", str(clip_path)])
    concat_inputs = "".join(f"[{idx}:v]" for idx in range(len(paths)))
    cmd.extend(
        [
            "-filter_complex",
            f"{concat_inputs}concat=n={len(paths)}:v=1:a=0[v]",
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    ffmpeg_commands.append(cmd)
    run_cmd(cmd, log_path=log_path, timeout_sec=command_timeout_sec, workdir=workdir, cwd=cwd)
    return output_path


def append_still_tail(
    ai_path: Path,
    still_path: Path,
    target_duration: float,
    output_path: Path,
    ffmpeg_commands: list[list[str]],
    log_path: Path | None,
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    ai_duration = get_media_duration(ai_path) or 0.0
    tail_duration = max(0.0, target_duration - ai_duration)
    still_tail_path = output_path.with_name(f"{output_path.stem}_still_tail.mp4")
    trim_clip(
        still_path,
        tail_duration,
        still_tail_path,
        ffmpeg_commands,
        log_path,
        command_timeout_sec,
        workdir=workdir,
        cwd=cwd,
    )
    concat_clips(
        [ai_path, still_tail_path],
        output_path,
        ffmpeg_commands,
        log_path,
        command_timeout_sec,
        workdir=workdir,
        cwd=cwd,
    )
    return trim_clip(
        output_path,
        target_duration,
        output_path,
        ffmpeg_commands,
        log_path,
        command_timeout_sec,
        workdir=workdir,
        cwd=cwd,
    )


def build_scene_final_clip(
    scene_id: str,
    still_scene_path: Path,
    ai_clip_path: Path | None,
    target_duration: float,
    output_path: Path,
    ffmpeg_commands: list[list[str]],
    log_path: Path | None,
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> tuple[Path, str, float | None]:
    ai_duration = get_media_duration(ai_clip_path) if ai_clip_path else None
    if ai_clip_path is None or not ai_clip_path.exists() or ai_duration is None or ai_duration <= 0:
        final_path = trim_clip(
            still_scene_path,
            target_duration,
            output_path,
            ffmpeg_commands,
            log_path,
            command_timeout_sec,
            workdir=workdir,
            cwd=cwd,
        )
        return final_path, "still_only", ai_duration

    if ai_duration >= target_duration - 0.01:
        final_path = trim_clip(
            ai_clip_path,
            target_duration,
            output_path,
            ffmpeg_commands,
            log_path,
            command_timeout_sec,
            workdir=workdir,
            cwd=cwd,
        )
        return final_path, "ai_only", ai_duration

    final_path = append_still_tail(
        ai_clip_path,
        still_scene_path,
        target_duration,
        output_path,
        ffmpeg_commands,
        log_path,
        command_timeout_sec,
        workdir=workdir,
        cwd=cwd,
    )
    return final_path, "ai_plus_still_tail", ai_duration


def build_final_scene_clip(
    scene_id: str,
    still_scene_path: Path,
    ai_clip_path: Path | None,
    target_duration: float,
    output_path: Path,
    ffmpeg_commands: list[list[str]],
    log_path: Path | None,
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> tuple[Path, str, float | None]:
    return build_scene_final_clip(
        scene_id=scene_id,
        still_scene_path=still_scene_path,
        ai_clip_path=ai_clip_path,
        target_duration=target_duration,
        output_path=output_path,
        ffmpeg_commands=ffmpeg_commands,
        log_path=log_path,
        command_timeout_sec=command_timeout_sec,
        workdir=workdir,
        cwd=cwd,
    )


def concat_scene_finals(
    scene_paths: list[Path],
    stitched_path: Path,
    log_path: Path | None,
    ffmpeg_commands: list[list[str]],
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> None:
    _concat_scenes(scene_paths, stitched_path, log_path, ffmpeg_commands, command_timeout_sec, workdir=workdir, cwd=cwd)


def add_transition_between_scene_finals(
    scene_paths: list[Path],
    stitched_path: Path,
    durations: list[float],
    fps: int,
    crossfade_duration: float,
    log_path: Path | None,
    ffmpeg_commands: list[list[str]],
    transition_types: list[str] | None = None,
    command_timeout_sec: float | None = None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> None:
    _crossfade_scenes(
        scene_paths=scene_paths,
        stitched_path=stitched_path,
        durations=durations,
        fps=fps,
        crossfade_duration=crossfade_duration,
        log_path=log_path,
        ffmpeg_commands=ffmpeg_commands,
        transition_types=transition_types,
        command_timeout_sec=command_timeout_sec,
        workdir=workdir,
        cwd=cwd,
    )


def validate_visual_timeline_duration(
    stitched_duration: float,
    expected_visual_duration: float,
    min_ratio: float = 0.8,
) -> tuple[bool, float]:
    if expected_visual_duration <= 0:
        return True, 1.0
    ratio = stitched_duration / expected_visual_duration if stitched_duration > 0 else 0.0
    return ratio >= min_ratio, ratio


def _zoompan_filter(scene, fps: int, width: int, height: int) -> str:
    motion = scene.motion
    if motion is None:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            "format=yuv420p"
        )

    zoom_start = motion.zoom_start if motion.type != "pan" else 1.0
    zoom_end = motion.zoom_end if motion.type != "pan" else 1.0
    x_start = motion.x_start
    x_end = motion.x_end
    y_start = motion.y if motion.type == "pan" and motion.y is not None else motion.y_start
    y_end = motion.y if motion.type == "pan" and motion.y is not None else motion.y_end

    # Render zoompan at 8× the target FPS internally so position is computed at
    # fine sub-frame resolution, then blend-downsample to the target rate with
    # minterpolate (mi_mode=blend).  Blend mode averages the 8 internal frames
    # into each output frame (temporal anti-aliasing), eliminating sub-pixel
    # timing jitter that causes visible stutter at native FPS.
    internal_fps = fps * 8
    frames = max(2, int(math.ceil(scene.duration * internal_fps)))

    # Sinusoidal ease-in/ease-out: t = (1 - cos(PI * on/frames)) / 2
    # Produces 0 at on=0 and 1 at on=frames with smooth acceleration/deceleration,
    # avoiding the abrupt mechanical starts and stops of linear interpolation.
    t_eased = f"(1-cos(PI*on/{frames}))/2"
    zoom_expr = f"{zoom_start}+({zoom_end}-{zoom_start})*{t_eased}"
    x_expr = f"({x_start}+({x_end}-{x_start})*{t_eased})*(iw-iw/zoom)"
    y_expr = f"({y_start}+({y_end}-{y_start})*{t_eased})*(ih-ih/zoom)"

    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={internal_fps},"
        f"minterpolate=fps={fps}:mi_mode=blend,"
        "format=yuv420p"
    )


def _render_scene(
    scene,
    output_path: Path,
    fps: int,
    width: int,
    height: int,
    log_path: Path | None,
    ffmpeg_commands: list[list[str]],
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> None:
    normalized_duration = _normalize_scene_duration(float(scene.duration), fps, scene.id)
    source_path = Path(scene.image_path)
    is_video = source_path.suffix.lower() in VIDEO_EXTENSIONS
    if is_video:
        try:
            source_duration = max(0.0, float(get_media_duration(source_path))) if source_path.exists() else 0.0
        except Exception:
            source_duration = 0.0
        pad_seconds = max(0.0, normalized_duration - source_duration)
        vf_parts = [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
            f"fps={fps}",
            "setpts=PTS-STARTPTS",
        ]
        if not bool(getattr(scene, "video_loop", False)) and pad_seconds > 0.01:
            vf_parts.append(f"tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}")
        vf_parts.append("format=yuv420p")
        filter_chain = ",".join(vf_parts)
        cmd = ["ffmpeg", "-y", "-fflags", "+genpts"]
        if bool(getattr(scene, "video_loop", False)):
            cmd.extend(["-stream_loop", "-1"])
        cmd.extend([
            "-i",
            scene.image_path,
            "-t",
            f"{normalized_duration:.6f}",
            "-vf",
            filter_chain,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ])
        ffmpeg_commands.append(cmd)
        primary_result = run_cmd(cmd, log_path=log_path, timeout_sec=command_timeout_sec, check=False, workdir=workdir, cwd=cwd)
        if not primary_result["ok"]:
            fallback_cmd = [
                "ffmpeg",
                "-y",
                "-fflags",
                "+genpts",
                "-err_detect",
                "ignore_err",
                "-i",
                scene.image_path,
                "-an",
                "-vf",
                filter_chain,
                "-t",
                f"{normalized_duration:.6f}",
                "-vsync",
                "cfr",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "24",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
            ffmpeg_commands.append(fallback_cmd)
            run_cmd(fallback_cmd, log_path=log_path, timeout_sec=command_timeout_sec, workdir=workdir, cwd=cwd)
        return

    filter_chain = _zoompan_filter(scene, fps, width, height)
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        scene.image_path,
        "-t",
        f"{normalized_duration:.6f}",
        "-vf",
        filter_chain,
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    ffmpeg_commands.append(cmd)
    primary_result = run_cmd(cmd, log_path=log_path, timeout_sec=command_timeout_sec, check=False, workdir=workdir, cwd=cwd)
    if not primary_result["ok"]:
        # Fallback: simpler scale/crop without zoompan (handles unusual image formats/sizes)
        simple_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            "format=yuv420p"
        )
        fallback_cmd = [
            "ffmpeg", "-y", "-loop", "1",
            "-i", scene.image_path,
            "-t", f"{normalized_duration:.6f}",
            "-vf", simple_filter,
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        ffmpeg_commands.append(fallback_cmd)
        run_cmd(fallback_cmd, log_path=log_path, timeout_sec=command_timeout_sec, workdir=workdir, cwd=cwd)


def _concat_scenes(
    scene_paths: list[Path],
    stitched_path: Path,
    log_path: Path | None,
    ffmpeg_commands: list[list[str]],
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> None:
    concat_list = stitched_path.with_suffix(".txt")
    concat_lines = [f"file '{path.as_posix()}'" for path in scene_paths]
    concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    concat_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(stitched_path),
    ]
    try:
        ffmpeg_commands.append(concat_cmd)
        run_cmd(concat_cmd, log_path=log_path, timeout_sec=command_timeout_sec, workdir=workdir, cwd=cwd)
    except subprocess.CalledProcessError:
        fallback_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            str(stitched_path),
        ]
        ffmpeg_commands.append(fallback_cmd)
        run_cmd(fallback_cmd, log_path=log_path, timeout_sec=command_timeout_sec, workdir=workdir, cwd=cwd)


def _crossfade_scenes(
    scene_paths: list[Path],
    stitched_path: Path,
    durations: list[float],
    fps: int,
    crossfade_duration: float,
    log_path: Path | None,
    ffmpeg_commands: list[list[str]],
    transition_types: list[str] | None = None,
    command_timeout_sec: float | None = None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> None:
    input_args: list[str] = []
    for path in scene_paths:
        input_args.extend(["-i", str(path)])

    filters: list[str] = []
    current_label = "[0:v]"
    offset = max(0.0, durations[0] - crossfade_duration)
    for idx in range(1, len(scene_paths)):
        next_label = f"[{idx}:v]"
        output_label = f"[v{idx}]"
        transition_name = _normalize_xfade_transition(
            transition_types[idx - 1] if transition_types and idx - 1 < len(transition_types) else "fade"
        )
        filters.append(
            f"{current_label}{next_label}xfade=transition={transition_name}:duration={crossfade_duration}:offset={offset}{output_label}"
        )
        current_label = output_label
        offset += max(0.0, durations[idx] - crossfade_duration)

    filter_complex = ";".join(filters)
    cmd = [
        "ffmpeg",
        "-y",
        *input_args,
        "-filter_complex",
        filter_complex,
        "-map",
        current_label,
        "-r",
        str(fps),
        "-vsync",
        "cfr",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-g",
        str(fps * 2),
        "-pix_fmt",
        "yuv420p",
        str(stitched_path),
    ]
    ffmpeg_commands.append(cmd)
    try:
        run_cmd(cmd, log_path=log_path, timeout_sec=command_timeout_sec, workdir=workdir, cwd=cwd)
    except subprocess.CalledProcessError as exc:
        _stderr = (exc.stderr or "")[-600:].strip()
        raise RuntimeError(f"xfade filter_complex failed: {_stderr}") from exc
    except Exception as exc:
        raise RuntimeError(f"xfade filter_complex failed: {exc}") from exc


def _subtitle_filter(subtitle_path: Path) -> str:
    path_str = subtitle_path.as_posix()
    escaped = path_str.replace("\\", "\\\\").replace(":", "\\:")
    return f"subtitles={escaped}:charenc=UTF-8"



def _assert_filter_complex_arg(cmd: list[str]) -> None:
    if "-filter_complex" not in cmd:
        return
    idx = cmd.index("-filter_complex")
    if idx + 1 >= len(cmd):
        raise ValueError("-filter_complex must be followed by a filtergraph argument")
    filter_graph = cmd[idx + 1]
    if not isinstance(filter_graph, str) or not filter_graph.strip():
        raise ValueError(f"filtergraph argument must be a non-empty string, got {filter_graph!r}")

def _ffmpeg_version() -> str:
    try:
        from .utils import resolve_ffmpeg_exe
        ffmpeg_exe = resolve_ffmpeg_exe()
        result = subprocess.run([ffmpeg_exe, "-version"], check=False, capture_output=True, text=True)
        version_line = (result.stdout or result.stderr).splitlines()
        return version_line[0].strip() if version_line else "unknown"
    except Exception:
        return "unknown"


def _tail_log_lines(log_path: Path | None, lines: int = 50) -> list[str]:
    if not log_path or not log_path.exists():
        return []
    with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
        content = handle.readlines()
    return [line.rstrip("\n") for line in content[-lines:]]


def _diagnostic_env() -> dict:
    """Gather environment info for the diagnostic report."""
    info: dict = {
        "ffmpeg_version": _ffmpeg_version(),
        "python_version": sys.version,
        "platform": platform.platform(),
    }
    try:
        total, _used, free = shutil.disk_usage("/")
        info["disk_free_gb"] = round(free / (1024 ** 3), 2)
        info["disk_total_gb"] = round(total / (1024 ** 3), 2)
    except Exception:
        pass
    return info


def _file_stat(path: str | None) -> dict:
    """Return existence and size info for a single file path."""
    if not path:
        return {"path": None, "exists": False, "size_bytes": 0}
    p = Path(path)
    try:
        stat = p.stat()
        return {"path": str(p), "exists": True, "size_bytes": stat.st_size}
    except OSError:
        return {"path": str(p), "exists": False, "size_bytes": 0}


def _scene_media_info(timeline: Timeline) -> list[dict]:
    """Return per-scene file metadata for the diagnostic report."""
    result = []
    for scene in timeline.scenes:
        info = _file_stat(scene.image_path)
        info["scene_id"] = scene.id
        info["duration"] = scene.duration
        result.append(info)
    return result


def _try_pull_project_assets_for_scene(scene_path: Path, project_root: Path, log_path: Path | None = None) -> bool:
    """Attempt to hydrate missing scene media from Supabase storage."""
    normalized_parts = scene_path.as_posix().split("/")
    try:
        data_idx = normalized_parts.index("data")
    except ValueError:
        return False
    if data_idx + 3 >= len(normalized_parts) or normalized_parts[data_idx + 1] != "projects":
        return False

    project_id = normalized_parts[data_idx + 2]
    if not project_id:
        return False

    project_dir = (project_root / "data" / "projects" / project_id).resolve()
    try:
        _sb_store = importlib.import_module("src.supabase_storage")
    except Exception:
        return False
    if not _sb_store.is_configured():
        return False

    fetched = _sb_store.pull_project_assets(project_id, project_dir)
    fetched_total = int(sum(fetched.values()))
    if log_path:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                "Missing scene media detected; attempted Supabase sync for "
                f"project '{project_id}' and downloaded {fetched_total} file(s).\n"
            )
    return fetched_total > 0 and scene_path.exists()



def _scene_cache_key(scene, fps: int, width: int, height: int) -> str:
    source_path = Path(scene.image_path)
    try:
        source_stat = source_path.stat()
    except OSError:
        source_stat = None
    payload = {
        "image_path": str(source_path.resolve()),
        "video_object_path": getattr(scene, "video_object_path", None),
        "size": source_stat.st_size if source_stat else None,
        "mtime_ns": source_stat.st_mtime_ns if source_stat else None,
        "duration": scene.duration,
        "fps": fps,
        "width": width,
        "height": height,
        "motion": scene.motion.model_dump() if scene.motion else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _resolve_scene_clip(
    scene,
    scene_out: Path,
    fps: int,
    width: int,
    height: int,
    cache_dir: Path,
    log_path: Path | None,
    ffmpeg_commands: list[list[str]],
    command_timeout_sec: float | None,
    workdir: Path | None = None,
    cwd: Path | None = None,
) -> bool:
    # Pre-made video clips (e.g. AI-generated) — copy directly, no Ken Burns
    if str(scene.image_path).endswith(".mp4") and Path(scene.image_path).exists():
        shutil.copy2(scene.image_path, scene_out)
        return False

    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_scene = cache_dir / f"{_scene_cache_key(scene, fps, width, height)}.mp4"
    if cached_scene.exists():
        shutil.copy2(cached_scene, scene_out)
        if log_path:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"Using cached scene clip for {scene.id}: {cached_scene}\n")
        return True

    _render_scene(scene, scene_out, fps, width, height, log_path, ffmpeg_commands, command_timeout_sec, workdir=workdir, cwd=cwd)
    if not scene_out.exists() or scene_out.stat().st_size == 0:
        raise RuntimeError(
            f"Scene render produced no output for scene {scene.id!r}: {scene_out}"
        )
    shutil.copy2(scene_out, cached_scene)
    return False


def render_video_from_timeline(
    timeline_path: str | Path,
    out_mp4_path: str | Path,
    log_path: str | Path | None = None,
    report_path: str | Path | None = None,
    command_timeout_sec: float | None = None,
    max_width: int = 1280,
    safe_mode: bool = False,
    render_warnings: list[str] | None = None,
    force_render_rebuild: bool = False,
) -> Path:
    ensure_ffmpeg_exists()

    timeline_content = Path(timeline_path).read_text(encoding="utf-8")
    timeline_hash = hashlib.sha256(timeline_content.encode("utf-8")).hexdigest()
    timeline = Timeline.model_validate_json(timeline_content)
    if not getattr(timeline.meta, "enable_motion", True):
        for scene in timeline.scenes:
            scene.motion = None
    if not timeline.scenes:
        raise ValueError("Timeline has no scenes to render.")

    scene_mapping_warnings: list[str] = []
    for idx, scene in enumerate(timeline.scenes, start=1):
        expected_scene_id = f"s{idx:02d}"
        source_path = str(scene.image_path or "")
        if source_path and not source_path.startswith("storage://"):
            name = Path(source_path).name.lower()
            if not name.startswith(expected_scene_id):
                scene_mapping_warnings.append(
                    f"Normalized scene id {scene.id!r} -> {expected_scene_id!r} for media {scene.image_path!r}"
                )
        if scene.id != expected_scene_id:
            scene.id = expected_scene_id

    output_path = ensure_parent_dir(out_mp4_path)
    staging_root = output_path.with_name(f"{output_path.stem}_staging").resolve()
    staging_root.mkdir(parents=True, exist_ok=True)
    render_dir = output_path.with_name(f"{output_path.stem}_render_logs").resolve()
    render_dir.mkdir(parents=True, exist_ok=True)
    log_file = Path(log_path).resolve() if log_path else render_dir / "render.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    report_file = Path(report_path).resolve() if report_path else output_path.with_name("render_report.json").resolve()
    project_root = Path.cwd().resolve()
    cache_dir = output_path.with_name("scene_cache")
    ffmpeg_commands: list[list[str]] = []
    render_error: str | None = None
    cache_hits = 0
    clean_deleted_outputs: list[str] = []
    tmp_output_path = output_path.with_name(f"{output_path.stem}_tmp{output_path.suffix}")
    if tmp_output_path.exists():
        tmp_output_path.unlink()
    if force_render_rebuild:
        candidate_paths = [output_path, tmp_output_path]
        timeline_resolved = Path(timeline_path).resolve()
        project_dir_guess = timeline_resolved.parent.parent if timeline_resolved.parent.name == "renders" else timeline_resolved.parent
        final_logs_dir = project_dir_guess / "renders" / "final_render_logs"
        candidate_paths.extend(
            [
                project_dir_guess / "renders" / "stitched.mp4",
                project_dir_guess / "renders" / "final_tmp.mp4",
                final_logs_dir / "scene_manifest.json",
            ]
        )
        for candidate in candidate_paths:
            if candidate.exists():
                candidate.unlink()
                clean_deleted_outputs.append(str(candidate))

    project_slug = (getattr(timeline.meta, "project_id", "") or "").strip() or Path(timeline_path).resolve().parent.name
    storage_buckets = {
        "images": str(get_secret("SUPABASE_IMAGES_BUCKET", "history-forge-images") or "history-forge-images"),
        "audio": str(get_secret("SUPABASE_AUDIO_BUCKET", "history-forge-audio") or "history-forge-audio"),
        "videos": str(get_secret("SUPABASE_VIDEOS_BUCKET", "generated-videos") or "generated-videos"),
    }
    timeline = stage_timeline_assets(
        timeline,
        staging_root=staging_root,
        project_slug=project_slug,
        bucket_images=storage_buckets["images"],
        bucket_audio=storage_buckets["audio"],
        bucket_videos=storage_buckets["videos"],
    )
    with log_file.open("a", encoding="utf-8") as h:
        if scene_mapping_warnings:
            h.write("=== SCENE MAPPING NORMALIZATION ===\n")
            for warning in scene_mapping_warnings:
                h.write(f"{warning}\n")
        h.write("=== STAGED SCENES ===\n")
        for s in timeline.scenes:
            h.write(f"{s.id} -> {s.image_path}\n")
    staged_files = sorted(str(path.resolve()) for path in staging_root.rglob("*") if path.is_file())

    try:
        with tempfile.TemporaryDirectory(prefix="history_forge_video_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            scenes_dir = tmp_path / "scenes"
            scenes_dir.mkdir(parents=True, exist_ok=True)

            width, height = _parse_resolution(timeline.meta.resolution)
            width, height = _apply_max_width(width, height, max_width=max_width)
            fps = timeline.meta.fps

            scene_paths: list[Path] = []
            durations: list[float] = []
            scene_duration_lookup: dict[str, float] = {}
            for scene in timeline.scenes:
                scene_path = Path(scene.image_path).resolve()
                if not scene_path.exists():
                    _try_pull_project_assets_for_scene(scene_path, project_root, log_path=log_file)
                if not scene_path.exists():
                    raise FileNotFoundError(f"Scene image not found: {scene.image_path}")
                scene.image_path = str(scene_path)
                normalized_duration = _normalize_scene_duration(float(scene.duration), fps, scene.id)
                scene_out = scenes_dir / f"{scene.id}.mp4"
                if _resolve_scene_clip(scene, scene_out, fps, width, height, cache_dir, log_file, ffmpeg_commands, command_timeout_sec, workdir=render_dir, cwd=project_root):
                    cache_hits += 1
                scene_paths.append(scene_out)
                durations.append(normalized_duration)
                scene_duration_lookup[scene.id] = normalized_duration

            # ── Build one final clip per scene slot (AI replace/fill) ───────
            final_scene_dir = tmp_path / "final_scene_clips"
            final_scene_dir.mkdir(parents=True, exist_ok=True)
            manifest_items: list[dict[str, object]] = []
            try:
                from src.workflow.project_io import load_project_payload
                _proj_payload = load_project_payload(project_slug)
                _opening = str(_proj_payload.get("ai_opening_clip_path", "") or "")
                _q2      = str(_proj_payload.get("ai_q2_clip_path", "") or "")
                _q3      = str(_proj_payload.get("ai_q3_clip_path", "") or "")
                _q4      = str(_proj_payload.get("ai_q4_clip_path", "") or "")
                # Fall back to Streamlit session state (UI path)
                try:
                    import streamlit as _st_render
                    _opening = _opening or str(_st_render.session_state.get("auto_ai_opening_clip") or "")
                    _q2      = _q2      or str(_st_render.session_state.get("auto_ai_q2_clip") or "")
                    _q3      = _q3      or str(_st_render.session_state.get("auto_ai_q3_clip") or "")
                    _q4      = _q4      or str(_st_render.session_state.get("auto_ai_q4_clip") or "")
                except Exception:
                    pass

                def _strip_audio(src: Path, dest: Path) -> Path:
                    """Re-encode AI clip to match scene format so xfade works correctly.

                    Uses the same target dimensions, fps, codec, and pixel format as the
                    rendered scene clips so the xfade filter never sees mismatched inputs.
                    Falls back to the source file if re-encoding fails.
                    """
                    import logging as _log_sa
                    try:
                        import subprocess as _sp
                        _ffmpeg_bin = resolve_ffmpeg_exe()
                        _result = _sp.run(
                            [
                                _ffmpeg_bin, "-y", "-i", str(src),
                                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps={fps},format=yuv420p",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                "-an",
                                str(dest),
                            ],
                            check=True, capture_output=True,
                        )
                        if dest.exists() and dest.stat().st_size > 0:
                            return dest
                        _log_sa.getLogger(__name__).warning("ai_clip_reencode produced empty file src=%s; using original", src)
                        return src
                    except Exception as _enc_err:
                        _stderr = getattr(_enc_err, "stderr", b"")
                        _stderr_str = (_stderr.decode("utf-8", errors="replace") if isinstance(_stderr, bytes) else str(_stderr))[-400:]
                        _log_sa.getLogger(__name__).warning(
                            "ai_clip_reencode_failed src=%s err=%s stderr=%s; using original (xfade may fail)",
                            src, _enc_err, _stderr_str,
                        )
                        return src

                ai_clip_map_raw = {
                    "s01": _opening,
                    "s03": _q2,
                    "s05": _q3,
                    "s07": _q4,
                }
                if _proj_payload:
                    ai_clip_map_raw = {
                        scene_id: str(_proj_payload.get(payload_key, "") or ai_clip_map_raw.get(scene_id, "") or "")
                        for scene_id, payload_key in AI_SCENE_CLIP_MAPPING.items()
                    }
                ai_clip_map: dict[str, str] = {}
                for scene_id, raw_path in ai_clip_map_raw.items():
                    if not raw_path:
                        continue
                    src_path = Path(raw_path)
                    if not src_path.exists():
                        continue
                    ai_scene_path = final_scene_dir / f"{scene_id}_ai_noaudio.mp4"
                    ai_clip_map[scene_id] = str(_strip_audio(src_path, ai_scene_path))

                final_scene_paths: list[Path] = []
                final_durations: list[float] = []
                for index, scene in enumerate(timeline.scenes):
                    scene_id = scene.id
                    still_scene_path = scene_paths[index]
                    target_duration = get_scene_target_duration(scene_id, scene_duration_lookup)
                    ai_clip_str = get_ai_clip_for_scene(scene_id, ai_clip_map)
                    ai_clip_path = Path(ai_clip_str) if ai_clip_str else None
                    final_scene_path = final_scene_dir / f"{scene_id}_final.mp4"
                    built_path, strategy_used, ai_duration = build_scene_final_clip(
                        scene_id=scene_id,
                        still_scene_path=still_scene_path,
                        ai_clip_path=ai_clip_path,
                        target_duration=target_duration,
                        output_path=final_scene_path,
                        ffmpeg_commands=ffmpeg_commands,
                        log_path=log_file,
                        command_timeout_sec=command_timeout_sec,
                        workdir=render_dir,
                        cwd=project_root,
                    )
                    final_duration = get_media_duration(built_path)
                    final_scene_paths.append(built_path)
                    final_durations.append(target_duration)
                    manifest_items.append(
                        {
                            "scene_id": scene_id,
                            "target_duration": target_duration,
                            "still_scene_path": str(still_scene_path),
                            "ai_clip_path": str(ai_clip_path) if ai_clip_path else "",
                            "ai_clip_exists": bool(ai_clip_path and ai_clip_path.exists()),
                            "ai_clip_duration": ai_duration,
                            "strategy_used": strategy_used,
                            "final_scene_clip_path": str(built_path),
                            "final_scene_clip_duration": final_duration,
                        }
                    )
                    if log_file:
                        with log_file.open("a", encoding="utf-8") as handle:
                            handle.write(
                                "final_scene scene_id=%s still_scene_path=%s ai_clip_path=%s target_duration=%.3f strategy=%s final_scene_clip=%s final_scene_duration=%s\n"
                                % (
                                    scene_id,
                                    still_scene_path,
                                    str(ai_clip_path) if ai_clip_path else "",
                                    target_duration,
                                    strategy_used,
                                    built_path,
                                    f"{final_duration:.3f}" if final_duration is not None else "unknown",
                                )
                            )
                scene_paths = final_scene_paths
                durations = final_durations
            except Exception as _clips_err:
                import logging as _log_cl
                _log_cl.getLogger(__name__).warning(
                    "ai_video_scene_slot_build failed project=%s err=%s", project_slug, _clips_err
                )

            manifest_dir = project_root / "data" / "projects" / project_slug / "renders" / "final_render_logs"
            manifest_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = manifest_dir / "scene_manifest.json"
            manifest_payload = {
                "project_id": project_slug,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "scenes": manifest_items,
            }
            manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            stitched_path = tmp_path / "stitched.mp4"
            stitched_manifest: dict[str, object] = {
                "project_id": project_slug,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "ordered_scene_final_inputs": [str(path) for path in scene_paths],
                "expected_total_duration": float(sum(durations)),
                "actual_stitched_duration": 0.0,
                "transitions_applied": False,
                "tpad_applied": False,
            }
            if log_file:
                with log_file.open("a", encoding="utf-8") as handle:
                    ordered_final_inputs = [str(path) for path in scene_paths]
                    scene_strategy_map = {item.get("scene_id", ""): item.get("strategy_used", "") for item in manifest_items}
                    handle.write(f"final_stitch ordered_final_inputs={ordered_final_inputs}\n")
                    handle.write(f"final_stitch scene_strategy_map={scene_strategy_map}\n")
                    handle.write(f"final_stitch force_render_rebuild={bool(force_render_rebuild)}\n")
                    handle.write(f"final_stitch deleted_or_ignored_outputs={clean_deleted_outputs}\n")
            if (not safe_mode) and timeline.meta.crossfade and len(scene_paths) > 1:
                effective_crossfade = _safe_crossfade_duration(durations, float(timeline.meta.crossfade_duration), fps)
                try:
                    if effective_crossfade > 0:
                        add_transition_between_scene_finals(
                            scene_paths,
                            stitched_path,
                            durations,
                            fps,
                            effective_crossfade,
                            log_file,
                            ffmpeg_commands,
                            transition_types=getattr(timeline.meta, "transition_types", []),
                            command_timeout_sec=command_timeout_sec,
                            workdir=render_dir,
                            cwd=project_root,
                        )
                        stitched_manifest["transitions_applied"] = True
                    else:
                        if log_file:
                            with log_file.open("a", encoding="utf-8") as handle:
                                handle.write(
                                    "Crossfade disabled because crossfade_duration is too large for one or more scene durations.\n"
                                )
                        concat_scene_finals(scene_paths, stitched_path, log_file, ffmpeg_commands, command_timeout_sec, workdir=render_dir, cwd=project_root)
                except (RuntimeError, subprocess.CalledProcessError) as _xfade_err:
                    _xfade_msg = f"Crossfade graph failed ({_xfade_err}); falling back to concat."
                    if log_file:
                        with log_file.open("a", encoding="utf-8") as handle:
                            handle.write(_xfade_msg + "\n")
                    import logging as _log_mod
                    _log_mod.getLogger(__name__).warning("xfade_fallback project=%s reason=%s", project_slug, _xfade_err)
                    if render_warnings is not None:
                        render_warnings.append(_xfade_msg)
                    concat_scene_finals(scene_paths, stitched_path, log_file, ffmpeg_commands, command_timeout_sec, workdir=render_dir, cwd=project_root)
            else:
                if safe_mode and timeline.meta.crossfade and len(scene_paths) > 1 and log_file:
                    with log_file.open("a", encoding="utf-8") as handle:
                        handle.write("Safe mode enabled: skipping crossfade and using concat.\n")
                concat_scene_finals(scene_paths, stitched_path, log_file, ffmpeg_commands, command_timeout_sec, workdir=render_dir, cwd=project_root)

            stitched_duration = ffprobe_duration(stitched_path)
            expected_visual_duration = float(sum(durations))
            timeline_ok, timeline_ratio = validate_visual_timeline_duration(stitched_duration, expected_visual_duration)
            stitched_manifest["actual_stitched_duration"] = stitched_duration

            srt_path = output_path.with_name("captions.srt")
            ass_path = output_path.with_name("captions.ass")
            subtitle_filter_applied = False
            music_mix_applied = False
            if timeline.meta.burn_captions:
                write_srt_file(srt_path, timeline)
                write_ass_file(ass_path, timeline)

            if timeline.meta.include_voiceover:
                if not timeline.meta.voiceover or not timeline.meta.voiceover.path:
                    raise FileNotFoundError("Voiceover is enabled but no voiceover path was provided.")
                voiceover_path = Path(timeline.meta.voiceover.path).resolve()
                if not voiceover_path.exists():
                    raise FileNotFoundError(f"Voiceover audio not found: {timeline.meta.voiceover.path}")
                timeline.meta.voiceover.path = str(voiceover_path)
            if timeline.meta.include_music and timeline.meta.music and timeline.meta.music.path:
                music_path = Path(timeline.meta.music.path).resolve()
                if not music_path.exists():
                    raise FileNotFoundError(f"Music file not found: {timeline.meta.music.path}")
                timeline.meta.music.path = str(music_path)

            voiceover_duration: float | None = None
            if timeline.meta.include_voiceover and timeline.meta.voiceover and timeline.meta.voiceover.path:
                voiceover_duration = get_media_duration(timeline.meta.voiceover.path)
            audio_target_duration = voiceover_duration if voiceover_duration is not None else timeline.total_duration

            include_audio = timeline.meta.include_voiceover or timeline.meta.include_music
            if include_audio:
                mixed_audio_path = tmp_path / "mixed.m4a"

                def _build_mix_audio_cmd(simplify_mix: bool = False) -> list[str]:
                    audio_plan = build_audio_mix_cmd(
                        timeline.meta,
                        audio_target_duration,
                        start_index=0,
                        force_simple_vo=simplify_mix,
                        simplify_mix=simplify_mix,
                    )
                    cmd = ["ffmpeg", "-y"]
                    cmd.extend(audio_plan.input_args)
                    cmd.extend(["-filter_complex", audio_plan.filter_complex])
                    cmd.extend(audio_plan.map_args)
                    cmd.extend(["-c:a", "aac", "-b:a", "192k", "-shortest", str(mixed_audio_path)])
                    _assert_filter_complex_arg(cmd)
                    return cmd

                mix_cmd = _build_mix_audio_cmd(simplify_mix=False)
                music_mix_applied = bool(timeline.meta.include_music and timeline.meta.music and timeline.meta.music.path)
                ffmpeg_commands.append(mix_cmd)
                mix_result = run_cmd(mix_cmd, log_path=log_file, timeout_sec=command_timeout_sec, check=False, workdir=render_dir, cwd=project_root)
                if not mix_result["ok"]:
                    retry_mix_cmd = _build_mix_audio_cmd(simplify_mix=True)
                    ffmpeg_commands.append(retry_mix_cmd)
                    run_cmd(retry_mix_cmd, log_path=log_file, timeout_sec=command_timeout_sec, workdir=render_dir, cwd=project_root)

                mixed_audio_duration = ffprobe_duration(mixed_audio_path)
                final_visual_clip_count = len(scene_paths)
                if log_file:
                    with log_file.open("a", encoding="utf-8") as handle:
                        handle.write(
                            "timeline_probe stitched_duration=%.3f mixed_audio_duration=%.3f expected_total_visual_duration=%.3f final_visual_clip_count=%d ratio=%.3f\n"
                            % (
                                stitched_duration,
                                mixed_audio_duration,
                                expected_visual_duration,
                                final_visual_clip_count,
                                timeline_ratio,
                            )
                        )

                debug_mode = bool(safe_mode or os.getenv("HF_RENDER_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"})
                allow_tpad_fallback = bool(os.getenv("HF_ALLOW_TPAD_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"})
                tpad_applied = False
                if not timeline_ok:
                    timeline_msg = (
                        "Visual timeline too short before final mux: "
                        f"stitched={stitched_duration:.3f}s expected={expected_visual_duration:.3f}s ratio={timeline_ratio:.3f} "
                        "(minimum allowed ratio=0.800)."
                    )
                    if debug_mode or not allow_tpad_fallback:
                        raise RuntimeError(timeline_msg)
                    if render_warnings is not None:
                        render_warnings.append(timeline_msg + " Using guarded tpad fallback.")
                    tpad_applied = True

                mux_cmd = ["ffmpeg", "-y", "-i", str(stitched_path), "-i", str(mixed_audio_path)]
                vf_filters: list[str] = []
                if tpad_applied and voiceover_duration is not None and stitched_duration > 0 and voiceover_duration > stitched_duration:
                    pad_seconds = max(0.0, voiceover_duration - stitched_duration)
                    vf_filters.append(f"tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}")
                    stitched_manifest["tpad_applied"] = True
                if timeline.meta.burn_captions and ass_path.exists():
                    vf_filters.append(_subtitle_filter(ass_path))
                    subtitle_filter_applied = True
                if vf_filters:
                    mux_cmd.extend(["-vf", ",".join(vf_filters)])
                mux_cmd.extend(
                    [
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "24",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                    ]
                )
                mux_cmd.extend(["-shortest"])
                mux_cmd.append(str(tmp_output_path))
                ffmpeg_commands.append(mux_cmd)
                run_cmd(mux_cmd, log_path=log_file, timeout_sec=command_timeout_sec, workdir=render_dir, cwd=project_root)
            else:
                cmd = ["ffmpeg", "-y", "-i", str(stitched_path)]
                if timeline.meta.burn_captions and ass_path.exists():
                    cmd.extend(["-vf", _subtitle_filter(ass_path)])
                    subtitle_filter_applied = True
                cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-movflags", "+faststart", str(tmp_output_path)])
                ffmpeg_commands.append(cmd)
                run_cmd(cmd, log_path=log_file, timeout_sec=command_timeout_sec, workdir=render_dir, cwd=project_root)

            stitched_manifest_path = manifest_dir / "stitched_manifest.json"
            stitched_manifest_path.write_text(json.dumps(stitched_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        if tmp_output_path.exists():
            tmp_output_path.replace(output_path)
            if timeline.meta.include_voiceover and timeline.meta.voiceover and timeline.meta.voiceover.path:
                expected_voiceover_duration = get_media_duration(timeline.meta.voiceover.path)
                rendered_duration = get_media_duration(output_path)
                if rendered_duration + 0.05 < expected_voiceover_duration:
                    raise RuntimeError(
                        "Rendered video is shorter than the voiceover "
                        f"({rendered_duration:.3f}s < {expected_voiceover_duration:.3f}s)."
                    )
        else:
            raise RuntimeError(f"Expected render output was not created: {tmp_output_path}")
    except Exception as exc:
        render_error = str(exc)
        raise
    finally:
        try:
            meta = timeline.meta
            report = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "failure" if render_error else "success",
                "success": render_error is None,
                "error_excerpt": render_error,
                "timeline_hash": timeline_hash,
                "command_timeout_sec": command_timeout_sec,
                "environment": _diagnostic_env(),
                "input_media": _scene_media_info(timeline),
                "audio": {
                    "voiceover": _file_stat(meta.voiceover.path if meta.voiceover else None),
                    "music": _file_stat(meta.music.path if meta.music else None),
                },
                "requested_aspect_ratio": meta.aspect_ratio,
                "resolved_output_size": meta.resolution,
                "subtitles_enabled": bool(meta.burn_captions),
                "subtitle_filter_applied": bool(locals().get("subtitle_filter_applied", False)),
                "effect_style": str(getattr(meta, "video_effects_style", "Ken Burns - Standard")),
                "music_enabled": bool(meta.include_music),
                "music_track": str(meta.music.path if meta.music and meta.music.path else ""),
                "music_mix_applied": bool(locals().get("music_mix_applied", False)),
                "output_path": str(output_path),
                "actual_output_size": _probe_video_dimensions(output_path) if output_path.exists() else "unknown",
                "scene_cache": {
                    "directory": str(cache_dir),
                    "hits": cache_hits,
                    "total_scenes": len(timeline.scenes),
                },
                "force_render_rebuild": bool(force_render_rebuild),
                "clean_deleted_outputs": clean_deleted_outputs,
                "ffmpeg_commands": [" ".join(cmd) for cmd in ffmpeg_commands],
                "tmp_output_path": str(tmp_output_path),
                "log_file": str(log_file),
                "render_dir": str(render_dir),
                "report_file": str(report_file),
                "staging": {
                    "staging_root": str(staging_root),
                    "file_count": len(staged_files),
                    "files": staged_files,
                    "buckets": storage_buckets,
                    "mapping_strategy": {
                        "local_prefix_images": os.getenv("LOCAL_PREFIX_IMAGES", "data/projects/{project}/assets/images/"),
                        "local_prefix_audio": os.getenv("LOCAL_PREFIX_AUDIO", "data/projects/{project}/assets/audio/"),
                        "storage_prefix_images": os.getenv("STORAGE_PREFIX_IMAGES", "{project}/"),
                        "storage_prefix_audio": os.getenv("STORAGE_PREFIX_AUDIO", "{project}/"),
                    },
                },
                "log_tail": _tail_log_lines(log_file, lines=50),
            }
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except Exception:
            # Never let report writing mask the original render exception
            pass

    return output_path
