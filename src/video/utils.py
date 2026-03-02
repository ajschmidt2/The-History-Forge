from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class FFmpegNotFoundError(RuntimeError):
    pass


def resolve_ffmpeg_exe() -> str:
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

    raise FileNotFoundError("ffmpeg executable not found. Install ffmpeg or ensure it is on PATH.")


def resolve_ffprobe_exe() -> str:
    env = os.environ.get("FFPROBE_PATH")
    if env and Path(env).exists():
        return env

    exe = shutil.which("ffprobe")
    if exe:
        return exe

    common = Path("/usr/bin/ffprobe")
    if common.exists():
        return str(common)

    try:
        ffmpeg_exe = Path(resolve_ffmpeg_exe())
        sibling = ffmpeg_exe.with_name("ffprobe")
        if sibling.exists():
            return str(sibling)
    except Exception:
        pass

    raise FileNotFoundError("ffprobe executable not found. Install ffmpeg (includes ffprobe) or ensure it is on PATH.")


def get_ffmpeg_exe() -> str:
    """Backward-compatible alias; prefer resolve_ffmpeg_exe."""
    return resolve_ffmpeg_exe()


def get_ffprobe_exe() -> str:
    """Backward-compatible alias; prefer resolve_ffprobe_exe."""
    return resolve_ffprobe_exe()


def ensure_ffmpeg_exists() -> None:
    try:
        ffmpeg_exe = resolve_ffmpeg_exe()
        subprocess.run([ffmpeg_exe, "-version"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise FFmpegNotFoundError(
            "FFmpeg is not installed. Add a packages.txt file with 'ffmpeg' to deploy on Streamlit Cloud."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegNotFoundError(
            "FFmpeg could not be executed. Ensure ffmpeg is installed and accessible in PATH."
        ) from exc


def run_ffmpeg(
    cmd: list[str],
    timeout_sec: float | None = None,
    workdir: str | Path | None = None,
    on_progress=None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Run ffmpeg/ffprobe command safely without bubbling process exceptions."""
    if not cmd or not cmd[0]:
        raise ValueError(f"Invalid ffmpeg/ffprobe command: {cmd!r}")
    try:
        resolved_cmd = list(cmd)
        first = Path(str(resolved_cmd[0])).name
        if first in {"ffmpeg", "ffprobe"}:
            from .ffmpeg_runner import run_ffmpeg_streaming

            exec_workdir = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="ffmpeg_run_"))
            exec_workdir.mkdir(parents=True, exist_ok=True)
            return run_ffmpeg_streaming(
                resolved_cmd,
                workdir=exec_workdir,
                timeout_sec=timeout_sec,
                on_progress=on_progress,
                debug_verbose=os.getenv("DEBUG_FFMPEG") == "1",
                cwd=Path(cwd) if cwd is not None else None,
            )

        result = subprocess.run(
            resolved_cmd,
            timeout=timeout_sec,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            cwd=str(Path(cwd).resolve()) if cwd is not None else None,
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
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": (
                f"Executable not found for command: {cmd!r}. "
                f"which(ffmpeg)={shutil.which('ffmpeg')!r}, which(ffprobe)={shutil.which('ffprobe')!r}. "
                f"Error: {exc}"
            ),
            "timed_out": False,
        }


def run_cmd(
    cmd: list[str],
    log_path: str | Path | None = None,
    check: bool = True,
    timeout_sec: float | None = None,
    on_progress=None,
    workdir: str | Path | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    def _abspath_if_file(s: str) -> str:
        p = Path(s)
        if p.is_absolute():
            return s
        if "/" in s or "\\" in s or p.suffix:
            return str(p.resolve())
        return s

    cmd2 = list(cmd)
    for idx, token in enumerate(cmd2):
        if token == "-i" and idx + 1 < len(cmd2):
            cmd2[idx + 1] = _abspath_if_file(str(cmd2[idx + 1]))

    if cmd2 and not str(cmd2[-1]).startswith("-"):
        cmd2[-1] = _abspath_if_file(str(cmd2[-1]))

    result = run_ffmpeg(cmd2, timeout_sec=timeout_sec, on_progress=on_progress, workdir=workdir, cwd=cwd)
    if log_path:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("$ " + " ".join(cmd2) + "\n")
            handle.write("cmd_json=" + json.dumps(cmd2, ensure_ascii=False) + "\n")
            if result.get("stdout_path"):
                handle.write(f"stdout_log_path={result['stdout_path']}\n")
            if result.get("stderr_path"):
                handle.write(f"stderr_log_path={result['stderr_path']}\n")
            if result.get("report_path"):
                handle.write(f"ffreport_path={result['report_path']}\n")
            if "-filter_complex" in cmd2:
                filter_idx = cmd2.index("-filter_complex") + 1
                if filter_idx < len(cmd2):
                    handle.write(f"filter_complex_repr={cmd2[filter_idx]!r}\n")
            if result["stderr"]:
                handle.write(result["stderr"] + "\n")
            if result["timed_out"]:
                handle.write(f"Command timed out after {timeout_sec}s\n")
    if check and not result["ok"]:
        if result["timed_out"]:
            timeout_details = [f"Command timed out after {timeout_sec}s: {' '.join(cmd2)}"]
            if result.get("workdir"):
                timeout_details.append(f"workdir={result['workdir']}")
            if result.get("stderr_path"):
                timeout_details.append(f"stderr_log={result['stderr_path']}")
            raise RuntimeError("; ".join(timeout_details))
        raise subprocess.CalledProcessError(
            returncode=int(result["returncode"] or 1),
            cmd=cmd2,
            output=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
        )
    return result


def get_media_duration(path: str | Path) -> float:
    media_path = Path(path).resolve()
    if not media_path.exists():
        return 0.0
    try:
        ffprobe_exe = resolve_ffprobe_exe()
    except FileNotFoundError:
        return 0.0
    cmd = [
        ffprobe_exe,
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
        return 0.0
    try:
        return float(result["stdout"].strip())
    except (TypeError, ValueError):
        return 0.0


def ensure_parent_dir(path: str | Path) -> Path:
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    return path_obj
