from __future__ import annotations

from pathlib import Path

from .timeline_schema import Timeline


def _format_srt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt_from_timeline(timeline: Timeline) -> str:
    lines: list[str] = []
    index = 1
    for scene in timeline.scenes:
        caption = (scene.caption or "").strip()
        if not caption:
            continue
        start = _format_srt_time(scene.start)
        end = _format_srt_time(scene.end)
        lines.extend([str(index), f"{start} --> {end}", caption, ""])
        index += 1
    return "\n".join(lines).strip() + ("\n" if lines else "")


def write_srt_file(output_path: str | Path, timeline: Timeline) -> Path:
    srt_text = build_srt_from_timeline(timeline)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(srt_text, encoding="utf-8")
    return output_path
