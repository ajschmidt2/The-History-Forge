from __future__ import annotations

import math
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .audio_mix import build_audio_mix_cmd
from .captions import write_ass_file, write_srt_file
from .timeline_schema import Timeline
from .utils import ensure_ffmpeg_exists, ensure_parent_dir, get_media_duration, run_cmd


VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
_ALLOWED_XFADE_TRANSITIONS = {"fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "slideright", "smoothleft", "smoothright", "circleopen", "circleclose", "distance"}


def _normalize_xfade_transition(name: str | None) -> str:
    transition = str(name or "fade").strip().lower()
    return transition if transition in _ALLOWED_XFADE_TRANSITIONS else "fade"


def _parse_resolution(resolution: str) -> tuple[int, int]:
    if "x" not in resolution:
        raise ValueError("Resolution must be formatted like 1080x1920")
    width, height = resolution.lower().split("x", maxsplit=1)
    return int(width), int(height)




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

    frames = max(1, int(math.ceil(scene.duration * fps)))
    zoom_expr = f"{zoom_start} + ({zoom_end} - {zoom_start})*on/{frames}"
    x_expr = f"({x_start} + ({x_end} - {x_start})*on/{frames})*(iw - iw/zoom)"
    y_expr = f"({y_start} + ({y_end} - {y_start})*on/{frames})*(ih - ih/zoom)"

    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={fps},"
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
    ffmpeg_commands.append(cmd)
    run_cmd(cmd, log_path=log_path, timeout_sec=command_timeout_sec, workdir=workdir, cwd=cwd)


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



def _scene_cache_key(scene, fps: int, width: int, height: int) -> str:
    source_path = Path(scene.image_path)
    try:
        source_stat = source_path.stat()
    except OSError:
        source_stat = None
    payload = {
        "image_path": str(source_path.resolve()),
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
    safe_mode: bool = True,
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
    output_path = ensure_parent_dir(out_mp4_path)
    render_dir = output_path.with_name(f"{output_path.stem}_render_logs").resolve()
    render_dir.mkdir(parents=True, exist_ok=True)
    log_file = Path(log_path).resolve() if log_path else render_dir / "render.log"
    report_file = Path(report_path).resolve() if report_path else output_path.with_name("render_report.json").resolve()
    project_root = Path.cwd().resolve()
    cache_dir = output_path.with_name("scene_cache")
    ffmpeg_commands: list[list[str]] = []
    render_error: str | None = None
    cache_hits = 0
    tmp_output_path = output_path.with_name(f"{output_path.stem}_tmp{output_path.suffix}")
    if tmp_output_path.exists():
        tmp_output_path.unlink()

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
            for scene in timeline.scenes:
                scene_path = Path(scene.image_path).resolve()
                if not scene_path.exists():
                    raise FileNotFoundError(f"Scene image not found: {scene.image_path}")
                scene.image_path = str(scene_path)
                normalized_duration = _normalize_scene_duration(float(scene.duration), fps, scene.id)
                scene_out = scenes_dir / f"{scene.id}.mp4"
                if _resolve_scene_clip(scene, scene_out, fps, width, height, cache_dir, log_file, ffmpeg_commands, command_timeout_sec, workdir=render_dir, cwd=project_root):
                    cache_hits += 1
                scene_paths.append(scene_out)
                durations.append(normalized_duration)

            stitched_path = tmp_path / "stitched.mp4"
            if (not safe_mode) and timeline.meta.crossfade and len(scene_paths) > 1:
                effective_crossfade = _safe_crossfade_duration(durations, float(timeline.meta.crossfade_duration), fps)
                try:
                    if effective_crossfade > 0:
                        _crossfade_scenes(
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
                    else:
                        if log_file:
                            with log_file.open("a", encoding="utf-8") as handle:
                                handle.write(
                                    "Crossfade disabled because crossfade_duration is too large for one or more scene durations.\n"
                                )
                        _concat_scenes(scene_paths, stitched_path, log_file, ffmpeg_commands, command_timeout_sec, workdir=render_dir, cwd=project_root)
                except (RuntimeError, subprocess.CalledProcessError):
                    if log_file:
                        with log_file.open("a", encoding="utf-8") as handle:
                            handle.write("Crossfade graph failed; falling back to concat.\n")
                    _concat_scenes(scene_paths, stitched_path, log_file, ffmpeg_commands, command_timeout_sec, workdir=render_dir, cwd=project_root)
            else:
                if safe_mode and timeline.meta.crossfade and len(scene_paths) > 1 and log_file:
                    with log_file.open("a", encoding="utf-8") as handle:
                        handle.write("Safe mode enabled: skipping crossfade and using concat.\n")
                _concat_scenes(scene_paths, stitched_path, log_file, ffmpeg_commands, command_timeout_sec, workdir=render_dir, cwd=project_root)

            srt_path = output_path.with_name("captions.srt")
            ass_path = output_path.with_name("captions.ass")
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
                ffmpeg_commands.append(mix_cmd)
                mix_result = run_cmd(mix_cmd, log_path=log_file, timeout_sec=command_timeout_sec, check=False, workdir=render_dir, cwd=project_root)
                if not mix_result["ok"]:
                    retry_mix_cmd = _build_mix_audio_cmd(simplify_mix=True)
                    ffmpeg_commands.append(retry_mix_cmd)
                    run_cmd(retry_mix_cmd, log_path=log_file, timeout_sec=command_timeout_sec, workdir=render_dir, cwd=project_root)

                mux_cmd = ["ffmpeg", "-y", "-i", str(stitched_path), "-i", str(mixed_audio_path)]
                stitched_duration = get_media_duration(stitched_path)
                vf_filters: list[str] = []
                if voiceover_duration is not None and stitched_duration > 0 and voiceover_duration > stitched_duration:
                    pad_seconds = max(0.0, voiceover_duration - stitched_duration)
                    vf_filters.append(f"tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}")
                if timeline.meta.burn_captions and ass_path.exists():
                    vf_filters.append(_subtitle_filter(ass_path))
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
                if voiceover_duration is not None:
                    mux_cmd.extend(["-t", f"{voiceover_duration:.3f}"])
                else:
                    mux_cmd.extend(["-shortest"])
                mux_cmd.append(str(tmp_output_path))
                ffmpeg_commands.append(mux_cmd)
                run_cmd(mux_cmd, log_path=log_file, timeout_sec=command_timeout_sec, workdir=render_dir, cwd=project_root)
            else:
                cmd = ["ffmpeg", "-y", "-i", str(stitched_path)]
                if timeline.meta.burn_captions and ass_path.exists():
                    cmd.extend(["-vf", _subtitle_filter(ass_path)])
                cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-movflags", "+faststart", str(tmp_output_path)])
                ffmpeg_commands.append(cmd)
                run_cmd(cmd, log_path=log_file, timeout_sec=command_timeout_sec, workdir=render_dir, cwd=project_root)

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
                "scene_cache": {
                    "directory": str(cache_dir),
                    "hits": cache_hits,
                    "total_scenes": len(timeline.scenes),
                },
                "ffmpeg_commands": [" ".join(cmd) for cmd in ffmpeg_commands],
                "tmp_output_path": str(tmp_output_path),
                "log_file": str(log_file),
                "render_dir": str(render_dir),
                "report_file": str(report_file),
                "log_tail": _tail_log_lines(log_file, lines=50),
            }
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except Exception:
            # Never let report writing mask the original render exception
            pass

    return output_path
