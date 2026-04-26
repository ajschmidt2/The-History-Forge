from __future__ import annotations

from pathlib import Path


CONTROL_DIR = Path(__file__).resolve().parent
SCRIPT_STYLE_FILE = CONTROL_DIR / "global_script_style.md"
VISUAL_STYLE_FILE = CONTROL_DIR / "global_visual_style.md"
OUTPUT_FORMAT_FILE = CONTROL_DIR / "global_output_format.md"


def _read_control_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_script_style() -> str:
    return _read_control_file(SCRIPT_STYLE_FILE)


def load_visual_style() -> str:
    return _read_control_file(VISUAL_STYLE_FILE)


def load_output_format() -> str:
    return _read_control_file(OUTPUT_FORMAT_FILE)


def load_all_controls() -> str:
    sections = [
        ("GLOBAL SCRIPT STYLE", load_script_style()),
        ("GLOBAL VISUAL STYLE", load_visual_style()),
        ("GLOBAL OUTPUT FORMAT", load_output_format()),
    ]
    return "\n\n".join(f"## {title}\n\n{content}" for title, content in sections if content).strip()
