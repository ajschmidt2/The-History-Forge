# History Forge Control Files

This folder contains global control files for the History Forge app. These files are not story-specific. They define shared standards that can be loaded into prompts as reusable context across the app.

## Files

- `global_script_style.md`
  - Defines the global writing and narration style for History Crossroads scripts.
  - Covers brand voice, tone, narrative structure, authenticity, engagement, CTA style, and writing constraints.

- `global_visual_style.md`
  - Defines the global visual identity for generated images, video prompts, scene prompts, and cinematic AI clip guidance.
  - Covers lighting, composition, historical grounding, texture, motion language, avoid rules, and reusable reinforcement keywords.

- `global_output_format.md`
  - Defines the default formatting contract for AI outputs.
  - Covers predictable markdown, script structure, scene blocks, prompt layouts, stable labels, and future parsing compatibility.

- `control_loader.py`
  - Provides simple Python helpers for loading each control document individually or combining all controls into one prompt-ready string.

## Usage

- Treat these as global control files for the History Forge app.
- Load them into prompts as shared context when generating scripts, scene prompts, visual prompts, video prompts, or structured outputs.
- Keep these files practical, implementation-friendly, and brand-consistent.
- Avoid story-specific examples in these files so they remain reusable.

## Future Overrides

Future override files can be added for specialized workflows, such as:

- YouTube Shorts style controls.
- Kids content controls.
- Alternate visual styles.
- Platform-specific output formats.
- Experimental prompt formats.

## UI Control Plan

- Add a "Global Controls" settings page or tab in the Streamlit app.
- Display each markdown file in an editable text area with save, reset, and reload actions.
- Validate that required headings are present before saving.
- Show a preview of the combined control context produced by `load_all_controls()`.
- Add per-run toggles for applying global script, visual, and output controls.
- Later, add named override profiles that layer on top of these defaults without editing the global baseline.
