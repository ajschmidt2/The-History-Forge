from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


def tail_text(path: Path, max_lines: int = 200) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return "".join(deque(handle, maxlen=max_lines))


def _ensure_ffmpeg_args(cmd: list[str], debug_verbose: bool = False) -> list[str]:
    updated = list(cmd)
    if not updated:
        return updated

    if "-hide_banner" not in updated:
        updated.insert(1, "-hide_banner")

    if "-loglevel" not in updated:
        level = "verbose" if debug_verbose else "level+info"
        updated[1:1] = ["-loglevel", level]

    insert_idx = 1
    if "-loglevel" in updated:
        insert_idx = updated.index("-loglevel") + 2

    if "-progress" not in updated:
        updated[insert_idx:insert_idx] = ["-progress", "pipe:1"]
        insert_idx += 2
    if "-nostats" not in updated:
        updated.insert(insert_idx, "-nostats")

    return updated


def run_ffmpeg_streaming(
    cmd: list[str],
    workdir: Path,
    timeout_sec: float | None = None,
    on_progress: callable | None = None,
    debug_verbose: bool = False,
) -> dict[str, Any]:
    if not cmd or not cmd[0]:
        raise ValueError(f"Invalid ffmpeg/ffprobe command: {cmd!r}")

    # Lazy import to avoid circular imports at module load time.
    from .utils import resolve_ffmpeg_exe, resolve_ffprobe_exe

    resolved_cmd = list(cmd)
    exe_name = Path(str(resolved_cmd[0])).name
    if exe_name == "ffmpeg":
        resolved_cmd[0] = resolve_ffmpeg_exe()
        resolved_cmd = _ensure_ffmpeg_args(resolved_cmd, debug_verbose=debug_verbose)
    elif exe_name == "ffprobe":
        resolved_cmd[0] = resolve_ffprobe_exe()

    workdir.mkdir(parents=True, exist_ok=True)
    stdout_path = workdir / "ffmpeg-stdout.log"
    stderr_path = workdir / "ffmpeg-stderr.log"
    report_path = workdir / "ffmpeg-report.log"

    env = os.environ.copy()
    env["FFREPORT"] = f"file={report_path}:level=32"

    with stdout_path.open("a", encoding="utf-8") as stdout_file, stderr_path.open("a", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            resolved_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(workdir),
            shell=False,
        )

        timed_out = False

        def _read_stdout() -> None:
            snapshot: dict[str, str] = {}
            assert process.stdout is not None
            for line in process.stdout:
                stdout_file.write(line)
                stdout_file.flush()
                text_line = line.strip()
                if "=" not in text_line:
                    continue
                key, value = text_line.split("=", 1)
                snapshot[key] = value
                if key == "progress" and value in {"continue", "end"}:
                    if on_progress:
                        on_progress(snapshot.copy())
                    snapshot.clear()

        def _read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_file.write(line)
                stderr_file.flush()

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        start_time = time.monotonic()
        while process.poll() is None:
            if timeout_sec is not None and (time.monotonic() - start_time) > timeout_sec:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            time.sleep(0.1)

        returncode = process.wait()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)

    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "stdout": tail_text(stdout_path, max_lines=200),
        "stderr": tail_text(stderr_path, max_lines=200),
        "timed_out": timed_out,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "report_path": str(report_path),
        "workdir": str(workdir),
    }
