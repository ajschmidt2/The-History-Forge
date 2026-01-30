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


def _format_ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_alignment(position: str) -> int:
    alignment_map = {"lower": 2, "center": 5, "top": 8}
    return alignment_map.get(position, 2)


def _ass_play_resolution(timeline: Timeline) -> tuple[int, int]:
    resolution = timeline.meta.resolution
    if "x" not in resolution:
        return 1920, 1080
    width, height = resolution.lower().split("x", maxsplit=1)
    try:
        return int(width), int(height)
    except ValueError:
        return 1920, 1080


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


def build_ass_from_timeline(timeline: Timeline) -> str:
    style = timeline.meta.caption_style
    play_res_x, play_res_y = _ass_play_resolution(timeline)
    primary = "&H00FFFFFF"
    secondary = "&H008A8A8A"
    outline = "&H00000000"
    back = "&H64000000"
    alignment = _ass_alignment(style.position)
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            "Style: Default,"
            f"{style.font},{style.font_size},{primary},{secondary},{outline},{back},"
            "0,0,0,0,100,100,0,0,1,2,0,"
            f"{alignment},40,40,{style.bottom_margin},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[str] = []
    for scene in timeline.scenes:
        caption = (scene.caption or "").strip()
        if not caption:
            continue
        words = caption.split()
        if not words:
            continue
        total_cs = max(1, int(round(scene.duration * 100)))
        base_cs = max(1, total_cs // len(words))
        remainder = total_cs - (base_cs * len(words))
        pieces = []
        for idx, word in enumerate(words):
            duration_cs = base_cs + (1 if idx < remainder else 0)
            pieces.append(f"{{\\k{duration_cs}}}{word}")
        text = " ".join(pieces)
        start = _format_ass_time(scene.start)
        end = _format_ass_time(scene.end)
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    return "\n".join(header + events) + "\n"


def write_ass_file(output_path: str | Path, timeline: Timeline) -> Path:
    ass_text = build_ass_from_timeline(timeline)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(ass_text, encoding="utf-8")
    return output_path


def write_srt_file(output_path: str | Path, timeline: Timeline) -> Path:
    srt_text = build_srt_from_timeline(timeline)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(srt_text, encoding="utf-8")
    return output_path
