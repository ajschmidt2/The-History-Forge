from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class FFmpegNotFoundError(RuntimeError):
    pass


def get_ffmpeg_exe() -> str:
    env = os.environ.get("FFMPEG_PATH")
    if env and Path(env).exists():
        return env

    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass

    raise FileNotFoundError("FFmpeg executable not found. Install ffmpeg or set FFMPEG_PATH env var.")


def ensure_ffmpeg_exists() -> None:
    try:
        ffmpeg_exe = get_ffmpeg_exe()
        subprocess.run([ffmpeg_exe, "-version"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise FFmpegNotFoundError(
            "FFmpeg is not installed. Add a packages.txt file with 'ffmpeg' to deploy on Streamlit Cloud."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegNotFoundError(
            "FFmpeg could not be executed. Ensure ffmpeg is installed and accessible in PATH."
        ) from exc


def run_ffmpeg(cmd: list[str], timeout_sec: float | None = None) -> dict[str, Any]:
    """Run ffmpeg/ffprobe command safely without bubbling process exceptions."""
    try:
        resolved_cmd = list(cmd)
        if resolved_cmd and Path(str(resolved_cmd[0])).name == "ffmpeg":
            resolved_cmd = [get_ffmpeg_exe(), *resolved_cmd[1:]]
        result = subprocess.run(
            resolved_cmd,
            timeout=timeout_sec,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
            "timed_out": True,
        }


def run_cmd(
    cmd: list[str],
    log_path: str | Path | None = None,
    check: bool = True,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    result = run_ffmpeg(cmd, timeout_sec=timeout_sec)
    if log_path:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("$ " + " ".join(cmd) + "\n")
            handle.write("cmd_json=" + json.dumps(cmd, ensure_ascii=False) + "\n")
            if "-filter_complex" in cmd:
                filter_idx = cmd.index("-filter_complex") + 1
                if filter_idx < len(cmd):
                    handle.write(f"filter_complex_repr={cmd[filter_idx]!r}\n")
            if result["stdout"]:
                handle.write(result["stdout"] + "\n")
            if result["stderr"]:
                handle.write(result["stderr"] + "\n")
            if result["timed_out"]:
                handle.write(f"Command timed out after {timeout_sec}s\n")
    if check and not result["ok"]:
        if result["timed_out"]:
            raise RuntimeError(f"Command timed out after {timeout_sec}s: {' '.join(cmd)}")
        raise subprocess.CalledProcessError(
            returncode=int(result["returncode"] or 1),
            cmd=cmd,
            output=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
        )
    return result


def get_media_duration(path: str | Path) -> float:
    media_path = Path(path)
    if not media_path.exists():
        return 0.0
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    result = run_ffmpeg(cmd)
    if not result["ok"]:
        raise RuntimeError(f"ffprobe failed for {path}: {result['stderr']}")
    return float(result["stdout"].strip())


def ensure_parent_dir(path: str | Path) -> Path:
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    return path_obj
