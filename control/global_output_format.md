# History Forge Global Output Format

## General Formatting Rules

- Outputs should be clean markdown when markdown is requested or useful.
- Do not include unnecessary preamble.
- Do not include extra commentary outside the requested structure.
- Keep sections predictable and machine-readable.
- Use stable labels and consistent ordering.
- Avoid markdown code fences unless the user explicitly asks for code or fenced output.
- Avoid hidden notes, assistant explanations, source notes, or production commentary in final generated content unless the workflow specifically asks for them.

## Script Output Structure

- **Title**
  - Provide a concise title when the workflow requests a titled script.
  - Keep it direct, documentary-friendly, and relevant to the subject.
- **Hook**
  - Open with the central tension, mystery, contradiction, or dramatic pressure point.
  - Keep the hook brief and easy to speak aloud.
- **Introduction**
  - Establish time, place, stakes, and the main historical forces at work.
  - Avoid long background dumps before the viewer understands why the story matters.
- **Main Body Sections**
  - Organize the story in a clear sequence of developments, decisions, conflicts, and consequences.
  - Each section should advance the story rather than repeat context.
  - Use transitions that connect cause and effect.
- **Conclusion**
  - Resolve the central question or tension.
  - Explain the meaning, consequence, or legacy of the story.
- **CTA**
  - Close with a natural invitation to continue watching, subscribe, or explore more History Crossroads content when a CTA is requested.
  - Keep the CTA short and consistent with the documentary tone.

## Scene Output Structure

- Each scene block should follow the same order.
- Use stable labels for parser-friendly output:
  - **Scene Number**
  - **Scene Title**
  - **Narration**
  - **Visual**
  - **Motion**
  - **Audio**
  - **Duration**
- Scene numbers should be sequential and unambiguous.
- Scene titles should be short and descriptive.
- Narration should contain only spoken voiceover text.
- Visual should describe what appears on screen.
- Motion should describe camera or subject movement when relevant.
- Audio should describe voiceover, music, ambience, or sound design only when the workflow needs it.
- Duration should be a practical estimate or explicit value when requested.

## Prompt Output Structure

- Use a standard layout for image and video prompt outputs:
  - **Prompt Title**
  - **Prompt Body**
  - **Style Notes**
  - **Aspect Ratio** if relevant
- Prompt titles should describe the scene or visual intent.
- Prompt bodies should be complete enough to send directly to an image or video model.
- Style notes should reinforce brand-level visual identity, historical grounding, lighting, texture, and exclusions.
- Aspect ratio should use stable values such as `16:9`, `9:16`, or `1:1` when needed by the pipeline.

## Consistency Rules

- Outputs must be easy to parse.
- Section titles should remain stable.
- Avoid changing labels unless intentionally versioned.
- Maintain consistent ordering.
- Use one label for one concept; do not alternate between synonyms such as "Voiceover," "Narration," and "Script Text" in the same structured format.
- Avoid mixing prose commentary into fields that should contain only data or final content.
- If a field is unavailable, leave it blank or use a predictable placeholder only when the workflow supports that behavior.

## Future Compatibility Note

- These standards are meant to support future pipeline automation, validation, structured parsing, prompt auditing, and UI controls.
- Stable labels and predictable sections make it easier to add validators, editors, exports, and alternate generation modes later.
- Future format versions should be introduced deliberately rather than by ad hoc prompt changes.

## Summary Enforcement Rule

- Treat this file as the default formatting contract for History Forge outputs. Apply it unless a workflow-specific format, parser requirement, or explicit user override takes priority.
