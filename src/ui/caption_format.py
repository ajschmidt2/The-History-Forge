import re


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _best_break(words: list[str], max_chars: int) -> int:
    length = 0
    candidates: list[int] = []
    punct_candidates: list[int] = []

    for idx, word in enumerate(words, start=1):
        extra = len(word) if idx == 1 else len(word) + 1
        if length + extra > max_chars:
            break
        length += extra
        candidates.append(idx)
        if re.search(r"[.!?,;:]$", word):
            punct_candidates.append(idx)

    if punct_candidates:
        return punct_candidates[-1]
    if candidates:
        return candidates[-1]
    return 1


def format_caption(text: str, max_lines: int = 2, max_chars_per_line: int = 32) -> str:
    normalized = _normalize(text)
    if not normalized:
        return ""

    if max_chars_per_line <= 0:
        return normalized

    # Keep historical default behavior at two lines while supporting larger line counts
    # for script-synced scene captions that should avoid early truncation.
    line_budget = max(1, int(max_lines or 1))

    words = normalized.split(" ")
    lines: list[str] = []

    while words and len(lines) < line_budget:
        count = _best_break(words, max_chars_per_line)
        line = " ".join(words[:count]).strip()
        if len(line) > max_chars_per_line:
            line = line[:max_chars_per_line].rstrip()
        lines.append(line)
        words = words[count:]

    if words and lines:
        # Preserve as much remaining text as possible in the final allowed line.
        remainder = " ".join(words).strip()
        final_line = lines[-1]
        joiner = " " if final_line else ""
        combined = f"{final_line}{joiner}{remainder}".strip()
        if len(combined) > max_chars_per_line:
            combined = combined[: max_chars_per_line - 1].rstrip(" ,;:") + "…"
        lines[-1] = combined

    return "\n".join(lines).strip()
