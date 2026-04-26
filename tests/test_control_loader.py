from control.control_loader import (
    load_all_controls,
    load_output_format,
    load_script_style,
    load_visual_style,
)


def test_control_loader_reads_global_markdown_files() -> None:
    script_style = load_script_style()
    visual_style = load_visual_style()
    output_format = load_output_format()
    combined = load_all_controls()

    assert "# History Forge Global Script Style" in script_style
    assert "# History Forge Global Visual Style" in visual_style
    assert "# History Forge Global Output Format" in output_format
    assert "GLOBAL SCRIPT STYLE" in combined
    assert "GLOBAL VISUAL STYLE" in combined
    assert "GLOBAL OUTPUT FORMAT" in combined
