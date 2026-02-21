from src.ui.tabs.generate_script import _clean_generated_script


def test_clean_generated_script_strips_notes_and_markdown_shell() -> None:
    raw = """```markdown
Script: The empire expanded through alliances and war.

## Notes to Verify
- Confirm date for treaty.
```"""

    cleaned = _clean_generated_script(raw)

    assert cleaned == "The empire expanded through alliances and war."


def test_clean_generated_script_keeps_only_revised_narration_block() -> None:
    raw = """The script provides a compelling overview but needs softer certainty.

Revised Script with Softened Claims:

In 1920s Harlem, Stephanie St. Clair rose to prominence.
She challenged corrupt systems and built community influence.

Notes to Verify: birthplace details and timeline specifics."""

    cleaned = _clean_generated_script(raw)

    assert cleaned == (
        "In 1920s Harlem, Stephanie St. Clair rose to prominence.\n"
        "She challenged corrupt systems and built community influence."
    )


def test_clean_generated_script_removes_sources_section_with_markdown_emphasis() -> None:
    raw = """Final Script:
The Nile shaped economies and political power for centuries.

**Sources Used:**
- Chronicle A
- Chronicle B
"""

    cleaned = _clean_generated_script(raw)

    assert cleaned == "The Nile shaped economies and political power for centuries."


def test_clean_generated_script_strips_leading_analysis_before_script_label() -> None:
    raw = """Analysis: tighten certainty and remove unsupported claims.

Narration: Carthage expanded maritime trade networks across the western Mediterranean.
It leveraged ports, treaties, and naval logistics to project influence.
"""

    cleaned = _clean_generated_script(raw)

    assert cleaned == (
        "Carthage expanded maritime trade networks across the western Mediterranean.\n"
        "It leveraged ports, treaties, and naval logistics to project influence."
    )

