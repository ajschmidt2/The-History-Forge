from __future__ import annotations

import math
import tempfile
from pathlib import Path

from .audio_mix import build_audio_mix_cmd
from .captions import write_ass_file, write_srt_file
from .timeline_schema import Timeline
from .utils import ensure_ffmpeg_exists, ensure_parent_dir, run_cmd


def _parse_resolution(resolution: str) -> tuple[int, int]:
    if "x" not in resolution:
        raise ValueError("Resolution must be formatted like 1080x1920")
    width, height = resolution.lower().split("x", maxsplit=1)
    return int(width), int(height)


def _zoompan_filter(scene, fps: int, width: int, height: int) -> str:
    motion = scene.motion
    if motion is None:
        zoom_start = 1.0
        zoom_end = 1.0
        x_start = 0.5
        x_end = 0.5
        y_start = 0.5
        y_end = 0.5
    else:
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
        f"scale={width * 1.08}:{height * 1.08}:force_original_aspect_ratio=increase,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={fps},"
        "format=yuv420p"
    )


def _render_scene(scene, output_path: Path, fps: int, width: int, height: int, log_path: Path | None) -> None:
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
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    run_cmd(cmd, log_path=log_path)


def _concat_scenes(scene_paths: list[Path], stitched_path: Path, log_path: Path | None) -> None:
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
        run_cmd(concat_cmd, log_path=log_path)
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
            "-pix_fmt",
            "yuv420p",
            str(stitched_path),
        ]
        run_cmd(fallback_cmd, log_path=log_path)


def _crossfade_scenes(
    scene_paths: list[Path],
    stitched_path: Path,
    durations: list[float],
    fps: int,
    crossfade_duration: float,
    log_path: Path | None,
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
        filters.append(
            f"{current_label}{next_label}xfade=transition=fade:duration={crossfade_duration}:offset={offset}{output_label}"
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
        "-pix_fmt",
        "yuv420p",
        str(stitched_path),
    ]
    run_cmd(cmd, log_path=log_path)


def _subtitle_filter(subtitle_path: Path) -> str:
    return f"subtitles='{subtitle_path.as_posix()}'"


def render_video_from_timeline(timeline_path: str | Path, out_mp4_path: str | Path, log_path: str | Path | None = None) -> Path:
    ensure_ffmpeg_exists()

    timeline = Timeline.parse_file(timeline_path)
    output_path = ensure_parent_dir(out_mp4_path)
    log_file = Path(log_path) if log_path else None

    with tempfile.TemporaryDirectory(prefix="history_forge_video_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        scenes_dir = tmp_path / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        width, height = _parse_resolution(timeline.meta.resolution)
        fps = timeline.meta.fps

        scene_paths: list[Path] = []
        durations: list[float] = []
        for scene in timeline.scenes:
            if not Path(scene.image_path).exists():
                raise FileNotFoundError(f"Scene image not found: {scene.image_path}")
            scene_out = scenes_dir / f"{scene.id}.mp4"
            _render_scene(scene, scene_out, fps, width, height, log_file)
            scene_paths.append(scene_out)
            durations.append(scene.duration)

        stitched_path = tmp_path / "stitched.mp4"
        if timeline.meta.crossfade and len(scene_paths) > 1:
            _crossfade_scenes(
                scene_paths,
                stitched_path,
                durations,
                fps,
                timeline.meta.crossfade_duration,
                log_file,
            )
        else:
            _concat_scenes(scene_paths, stitched_path, log_file)

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
            audio_plan = build_audio_mix_cmd(timeline.meta, timeline.total_duration)
            cmd = ["ffmpeg", "-y", "-i", str(stitched_path)]
            cmd.extend(audio_plan.input_args)
            if timeline.meta.burn_captions:
                cmd.extend(["-vf", _subtitle_filter(ass_path)])
            cmd.extend(["-filter_complex", audio_plan.filter_complex])
            cmd.extend(["-map", "0:v:0"])
            cmd.extend(audio_plan.map_args)
            cmd.extend(["-c:v", "libx264", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(output_path)])
            run_cmd(cmd, log_path=log_file)
        else:
            cmd = ["ffmpeg", "-y", "-i", str(stitched_path)]
            if timeline.meta.burn_captions:
                cmd.extend(["-vf", _subtitle_filter(ass_path)])
            cmd.extend(["-c:v", "libx264", "-movflags", "+faststart", str(output_path)])
            run_cmd(cmd, log_path=log_file)

    return output_path
