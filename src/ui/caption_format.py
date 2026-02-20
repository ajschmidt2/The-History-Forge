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

    words = normalized.split(" ")
    if len(normalized) <= max_chars_per_line or max_lines <= 1:
        return normalized[:max_chars_per_line].strip()

    first_count = _best_break(words, max_chars_per_line)
    first_line = " ".join(words[:first_count]).strip()
    remainder = words[first_count:]
    if not remainder:
        return first_line

    second_count = _best_break(remainder, max_chars_per_line)
    second_line = " ".join(remainder[:second_count]).strip()

    if second_count < len(remainder):
        trimmed = second_line[: max(1, max_chars_per_line - 1)].rstrip(" ,;:")
        second_line = f"{trimmed}…"

    if len(first_line) > max_chars_per_line:
        first_line = first_line[:max_chars_per_line].rstrip()
    if len(second_line) > max_chars_per_line:
        second_line = second_line[: max_chars_per_line - 1].rstrip() + "…"

    return f"{first_line}\n{second_line}".strip()
