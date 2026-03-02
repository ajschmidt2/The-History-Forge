from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = {
    "src/config/secrets.py",
    "tools/check_secrets_usage.py",
}
PATTERNS = [
    re.compile(r"\bst\.secrets\b"),
    re.compile(r"os\.getenv\((?:\"|\')(?:SUPABASE|OPENAI_API_KEY|openai_api_key)"),
]


def main() -> int:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        for idx, line in enumerate(text.splitlines(), start=1):
            if any(p.search(line) for p in PATTERNS):
                violations.append(f"{rel}:{idx}: {line.strip()}")

    if violations:
        print("Forbidden direct secret access found:")
        for item in violations:
            print(f" - {item}")
        return 1

    print("No forbidden direct secret access patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
