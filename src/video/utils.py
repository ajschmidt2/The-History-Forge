from __future__ import annotations

import subprocess
from pathlib import Path


class FFmpegNotFoundError(RuntimeError):
    pass


def ensure_ffmpeg_exists() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError(
            "FFmpeg is not installed. Add a packages.txt file with 'ffmpeg' to deploy on Streamlit Cloud."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegNotFoundError(
            "FFmpeg could not be executed. Ensure ffmpeg is installed and accessible in PATH."
        ) from exc


def run_cmd(cmd: list[str], log_path: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if log_path:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("$ " + " ".join(cmd) + "\n")
            if result.stdout:
                handle.write(result.stdout + "\n")
            if result.stderr:
                handle.write(result.stderr + "\n")
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}")
    return result


def get_media_duration(path: str | Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr}")
    return float(result.stdout.strip())


def ensure_parent_dir(path: str | Path) -> Path:
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    return path_obj
