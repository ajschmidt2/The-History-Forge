import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ui.caption_format import format_caption


def _assert_limits(caption: str, max_lines: int = 2, max_chars: int = 32) -> None:
    lines = caption.split("\n") if caption else []
    assert len(lines) <= max_lines
    for line in lines:
        assert len(line) <= max_chars


def test_format_caption_trims_and_collapses_whitespace() -> None:
    caption = format_caption("  Hello   world.   ")
    assert caption == "Hello world."
    _assert_limits(caption)


def test_format_caption_two_line_limit_with_long_text() -> None:
    caption = format_caption(
        "This is a long subtitle sentence, with punctuation. It should wrap nicely into two readable lines for output."
    )
    _assert_limits(caption)
    assert "\n" in caption


def test_format_caption_prefers_punctuation_break_when_possible() -> None:
    caption = format_caption("Alpha beta gamma, delta epsilon zeta eta theta iota kappa lambda")
    first_line = caption.split("\n")[0]
    assert first_line.endswith(",")
    _assert_limits(caption)


def test_format_caption_empty_input_returns_empty_string() -> None:
    assert format_caption("   ") == ""


def test_format_caption_truncates_to_two_lines() -> None:
    very_long = "word " * 200
    caption = format_caption(very_long)
    _assert_limits(caption)


def test_format_caption_with_larger_line_budget_preserves_tail_text() -> None:
    text = "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
    caption = format_caption(text, max_lines=12, max_chars_per_line=16)

    assert "sixteen" in caption
    lines = caption.split("\n")
    assert len(lines) <= 12
    assert all(len(line) <= 16 for line in lines)
