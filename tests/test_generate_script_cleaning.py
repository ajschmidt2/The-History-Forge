from src.ui.tabs.generate_script import _clean_generated_script


def test_clean_generated_script_strips_notes_and_markdown_shell() -> None:
    raw = """```markdown
Script: The empire expanded through alliances and war.

## Notes to Verify
- Confirm date for treaty.
```"""

    cleaned = _clean_generated_script(raw)

    assert cleaned == "The empire expanded through alliances and war."
