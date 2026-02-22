from __future__ import annotations

import math
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .audio_mix import build_audio_mix_cmd
from .captions import write_ass_file, write_srt_file
from .timeline_schema import Timeline
from .utils import ensure_ffmpeg_exists, ensure_parent_dir, run_cmd


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
) -> None:
    source_path = Path(scene.image_path)
    is_video = source_path.suffix.lower() in VIDEO_EXTENSIONS
    if is_video:
        filter_chain = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            "format=yuv420p"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            scene.image_path,
            "-t",
            f"{scene.duration}",
            "-vf",
            filter_chain,
            "-r",
            str(fps),
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
    else:
        filter_chain = _zoompan_filter(scene, fps, width, height)
        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            scene.image_path,
            "-t",
            f"{scene.duration}",
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
    run_cmd(cmd, log_path=log_path, timeout_sec=command_timeout_sec)


def _concat_scenes(
    scene_paths: list[Path],
    stitched_path: Path,
    log_path: Path | None,
    ffmpeg_commands: list[list[str]],
    command_timeout_sec: float | None,
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
        run_cmd(concat_cmd, log_path=log_path, timeout_sec=command_timeout_sec)
    except RuntimeError:
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
        run_cmd(fallback_cmd, log_path=log_path, timeout_sec=command_timeout_sec)


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
    run_cmd(cmd, log_path=log_path, timeout_sec=command_timeout_sec)


def _subtitle_filter(subtitle_path: Path) -> str:
    path_str = subtitle_path.as_posix()
    escaped = path_str.replace("\\", "\\\\").replace(":", "\\:")
    return f"subtitles={escaped}:charenc=UTF-8"



def _assert_filter_complex_arg(cmd: list[str]) -> None:
    if "-filter_complex" not in cmd:
        return
    idx = cmd.index("-filter_complex")
    assert idx + 1 < len(cmd), "-filter_complex must be followed by a filtergraph argument"
    filter_graph = cmd[idx + 1]
    assert isinstance(filter_graph, str), "filtergraph argument must be a string"
    assert filter_graph.strip(), "filtergraph argument must not be empty"

def _ffmpeg_version() -> str:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    version_line = (result.stdout or result.stderr).splitlines()
    return version_line[0].strip() if version_line else "unknown"


def _tail_log_lines(log_path: Path | None, lines: int = 50) -> list[str]:
    if not log_path or not log_path.exists():
        return []
    with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
        content = handle.readlines()
    return [line.rstrip("\n") for line in content[-lines:]]



def _scene_cache_key(scene, fps: int, width: int, height: int) -> str:
    source_path = Path(scene.image_path)
    source_stat = source_path.stat()
    payload = {
        "image_path": str(source_path.resolve()),
        "size": source_stat.st_size,
        "mtime_ns": source_stat.st_mtime_ns,
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
) -> bool:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_scene = cache_dir / f"{_scene_cache_key(scene, fps, width, height)}.mp4"
    if cached_scene.exists():
        shutil.copy2(cached_scene, scene_out)
        if log_path:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"Using cached scene clip for {scene.id}: {cached_scene}\n")
        return True

    _render_scene(scene, scene_out, fps, width, height, log_path, ffmpeg_commands, command_timeout_sec)
    shutil.copy2(scene_out, cached_scene)
    return False


def render_video_from_timeline(
    timeline_path: str | Path,
    out_mp4_path: str | Path,
    log_path: str | Path | None = None,
    report_path: str | Path | None = None,
    command_timeout_sec: float | None = None,
    max_width: int = 1280,
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
    log_file = Path(log_path) if log_path else None
    report_file = Path(report_path) if report_path else output_path.with_name("render_report.json")
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
                if not Path(scene.image_path).exists():
                    raise FileNotFoundError(f"Scene image not found: {scene.image_path}")
                scene_out = scenes_dir / f"{scene.id}.mp4"
                if _resolve_scene_clip(scene, scene_out, fps, width, height, cache_dir, log_file, ffmpeg_commands, command_timeout_sec):
                    cache_hits += 1
                scene_paths.append(scene_out)
                durations.append(scene.duration)

            stitched_path = tmp_path / "stitched.mp4"
            if timeline.meta.crossfade and len(scene_paths) > 1:
                try:
                    _crossfade_scenes(
                        scene_paths,
                        stitched_path,
                        durations,
                        fps,
                        timeline.meta.crossfade_duration,
                        log_file,
                        ffmpeg_commands,
                        transition_types=getattr(timeline.meta, "transition_types", []),
                        command_timeout_sec=command_timeout_sec,
                    )
                except RuntimeError:
                    if log_file:
                        with log_file.open("a", encoding="utf-8") as handle:
                            handle.write("Crossfade graph failed; falling back to concat.\n")
                    _concat_scenes(scene_paths, stitched_path, log_file, ffmpeg_commands, command_timeout_sec)
            else:
                _concat_scenes(scene_paths, stitched_path, log_file, ffmpeg_commands, command_timeout_sec)

            srt_path = output_path.with_name("captions.srt")
            ass_path = output_path.with_name("captions.ass")
            write_srt_file(srt_path, timeline)
            write_ass_file(ass_path, timeline)

            if timeline.meta.include_voiceover:
                if not timeline.meta.voiceover or not timeline.meta.voiceover.path:
                    raise FileNotFoundError("Voiceover is enabled but no voiceover path was provided.")
                if not Path(timeline.meta.voiceover.path).exists():
                    raise FileNotFoundError(f"Voiceover audio not found: {timeline.meta.voiceover.path}")
            if (
                timeline.meta.include_music
                and timeline.meta.music
                and timeline.meta.music.path
                and not Path(timeline.meta.music.path).exists()
            ):
                raise FileNotFoundError(f"Music file not found: {timeline.meta.music.path}")

            include_audio = timeline.meta.include_voiceover or timeline.meta.include_music
            if include_audio:
                mixed_audio_path = tmp_path / "mixed.m4a"

                def _build_mix_audio_cmd(simplify_mix: bool = False) -> list[str]:
                    audio_plan = build_audio_mix_cmd(
                        timeline.meta,
                        timeline.total_duration,
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
                mix_result = run_cmd(mix_cmd, log_path=log_file, timeout_sec=command_timeout_sec, check=False)
                if not mix_result["ok"]:
                    retry_mix_cmd = _build_mix_audio_cmd(simplify_mix=True)
                    ffmpeg_commands.append(retry_mix_cmd)
                    run_cmd(retry_mix_cmd, log_path=log_file, timeout_sec=command_timeout_sec)

                mux_cmd = ["ffmpeg", "-y", "-i", str(stitched_path), "-i", str(mixed_audio_path)]
                if timeline.meta.burn_captions:
                    mux_cmd.extend(["-vf", _subtitle_filter(ass_path)])
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
                        "-shortest",
                        "-movflags",
                        "+faststart",
                        str(tmp_output_path),
                    ]
                )
                ffmpeg_commands.append(mux_cmd)
                run_cmd(mux_cmd, log_path=log_file, timeout_sec=command_timeout_sec)
            else:
                cmd = ["ffmpeg", "-y", "-i", str(stitched_path)]
                if timeline.meta.burn_captions:
                    cmd.extend(["-vf", _subtitle_filter(ass_path)])
                cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-movflags", "+faststart", str(tmp_output_path)])
                ffmpeg_commands.append(cmd)
                run_cmd(cmd, log_path=log_file, timeout_sec=command_timeout_sec)

        if tmp_output_path.exists():
            tmp_output_path.replace(output_path)
        else:
            raise RuntimeError(f"Expected render output was not created: {tmp_output_path}")
    except Exception as exc:
        render_error = str(exc)
        raise
    finally:
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ffmpeg_commands": [" ".join(cmd) for cmd in ffmpeg_commands],
            "timeline_hash": timeline_hash,
            "environment": {"ffmpeg_version": _ffmpeg_version()},
            "command_timeout_sec": command_timeout_sec,
            "status": "failure" if render_error else "success",
            "success": render_error is None,
            "error_excerpt": render_error,
            "scene_cache": {"directory": str(cache_dir), "hits": cache_hits, "total_scenes": len(timeline.scenes)},
            "tmp_output_path": str(tmp_output_path),
            "log_tail": _tail_log_lines(log_file, lines=50),
        }
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return output_path
