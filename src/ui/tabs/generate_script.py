import json
import traceback
import re
from pathlib import Path

import streamlit as st
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from src.research.web_research import Source, search_topic, summarize_sources
from src.script.refine import flag_uncertain_claims, refine_for_clarity, refine_for_retention
from src.supabase_storage import upload_script
from src.ui.state import active_project_id, clear_downstream, openai_error_message, save_project_state, script_ready
from utils import edit_script_with_direction, generate_lucky_topic, generate_outline, generate_research_brief, generate_script, generate_script_from_outline, split_script_into_scene_strings

_OPENAI_API_ERRORS = (AuthenticationError, RateLimitError, APIConnectionError, APIError)




def _show_openai_error(exc: Exception) -> None:
    """Display an OpenAI error.  Known API errors get a clean one-line message; unexpected
    errors include the traceback for easier debugging."""
    msg = openai_error_message(exc)
    if isinstance(exc, _OPENAI_API_ERRORS):
        st.error(msg)
    else:
        tb = traceback.format_exc()
        st.error(f"{msg}\n\nTRACEBACK:\n{tb}")


def _save_script_to_supabase(project_id: str, script_text: str) -> None:
    """Upload the final script text to Supabase Storage (best-effort, silent on failure)."""
    try:
        upload_script(project_id, script_text)
    except Exception:
        pass


def _save_outline_json(outline_text: str) -> None:
    outline_dir = Path("data/projects") / active_project_id()
    outline_dir.mkdir(parents=True, exist_ok=True)
    outline_path = outline_dir / "outline.json"
    try:
        parsed = json.loads(outline_text)
    except json.JSONDecodeError:
        return
    outline_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")



def _apply_refinement_passes(script: str) -> str:
    revised = script
    if st.session_state.run_clarity_pass:
        revised = refine_for_clarity(revised)
    if st.session_state.run_retention_pass:
        revised = refine_for_retention(revised)
    if st.session_state.run_safety_pass:
        revised = flag_uncertain_claims(revised, st.session_state.research_brief_text)
    return revised


def _extract_narration_from_structured_script(text: str) -> str:
    """Strip scene markers and visual intent lines from structured LLM output.

    When the model returns the scene-delimited format (SCENE XX | title /
    NARRATION: ... / VISUAL INTENT: ... / END SCENE XX / ---SCENE_BREAK---),
    this extracts only the NARRATION text and returns it as plain paragraphs
    separated by blank lines.  Returns the original text unchanged if no
    NARRATION: markers are found.
    """
    if not re.search(r"(?im)^NARRATION:", text):
        return text

    parts: list[str] = []
    current: list[str] = []
    state = "between"  # "between" | "narration" | "visual"

    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()

        if stripped == "---SCENE_BREAK---":
            if current:
                parts.append(" ".join(current).strip())
                current = []
            state = "between"
            continue

        if re.match(r"(?i)^(?:SCENE\s+\d+\b|END\s+SCENE\s+\d+\b)", stripped):
            state = "between"
            continue

        if re.match(r"(?i)^NARRATION:", stripped):
            if current:
                parts.append(" ".join(current).strip())
                current = []
            content = re.sub(r"(?i)^NARRATION:\s*", "", stripped).strip()
            if content:
                current.append(content)
            state = "narration"
            continue

        if re.match(r"(?i)^VISUAL INTENT:", stripped):
            state = "visual"
            continue

        if state == "narration" and stripped:
            current.append(stripped)

    if current:
        parts.append(" ".join(current).strip())

    if not parts:
        return text

    return "\n\n".join(p for p in parts if p)


def _clean_generated_script(script: str, light: bool = False) -> str:
    """Clean a generated or edited script.

    Parameters
    ----------
    script:
        Raw script text to clean.
    light:
        When *True*, skip the aggressive structural stripping that is designed
        for fresh LLM output containing SCENE markers / commentary.  Use this
        flag when the input is already a cleaned plain-text script (e.g. after
        "Apply Direction" editing) so that valid narration is never stripped.
    """
    text = str(script or "").strip()
    if not text:
        return ""

    # Strip markdown code fences produced by some models.
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    if light:
        # Light mode: only strip obvious LLM preamble / fences; preserve the
        # narration exactly as returned.  This is safe to use on scripts that
        # have already been cleaned once (e.g. after "Apply Direction").
        raw_lines = [line.rstrip() for line in text.splitlines()]
        cleaned_lines: list[str] = []
        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line:
                if cleaned_lines and cleaned_lines[-1] != "":
                    cleaned_lines.append("")
                continue
            # Only strip obvious LLM meta-commentary lines.
            if re.match(r"(?i)^\s*(?:here(?:’|’)s|below\s+is|let\s+me\s+know|i\s+can\s+also)\b", line):
                continue
            if re.match(r"(?i)^\s*(?:visual|b-?roll|on-?screen(?:\s+text)?|sfx|music|camera|transition)\s*:", line):
                continue
            cleaned_lines.append(line)
        candidate = "\n".join(cleaned_lines)
        candidate = re.sub(r"\n{3,}", "\n\n", candidate).strip()
        # Never return empty — fall back to the original stripped text.
        return candidate if candidate else text

    # --- Full cleaning mode (for fresh LLM output with SCENE markers etc.) ---

    # Strip scene structure markers, keeping only NARRATION text.
    text = _extract_narration_from_structured_script(text)

    # If the model wrapped narration with commentary, prefer explicit script blocks.
    revised_block = re.search(
        r"(?is)(?:revised\s+script(?:\s+with\s+softened\s+claims)?|clean\s+script|final\s+script|script\s+only)\s*:\s*(.+)",
        text,
    )
    if revised_block:
        text = revised_block.group(1).strip()

    # Drop any leading analysis text before clear script labels.
    leading_labels = re.search(r"(?im)^\s*(?:script|narration|voiceover\s+script)\s*:\s*", text)
    if leading_labels:
        text = text[leading_labels.start():].strip()

    # Remove trailing verification/source/meta sections with or without markdown headers.
    trailing_section_pattern = (
        r"(?im)^\s*(?:#{1,6}\s*|\*{0,2})"
        r"(?:notes?\s+to\s+verify|verification(?:\s+notes?)?|fact-?check(?:ing)?(?:\s+notes?)?|"
        r"sources?(?:\s+used)?|citations?|references?|editor(?:’s)?\s+notes?|"
        r"production\s+notes?|visual\s+notes?)"
        r"(?:\*{0,2})\s*:?\s*$"
    )
    split_lines = re.split(trailing_section_pattern, text, maxsplit=1)
    text = split_lines[0].strip() if split_lines else text

    # Also handle inline suffixes like "Notes to Verify: ..." on the same line.
    text = re.split(
        r"(?is)\n?\s*\*{0,2}(?:notes?\s+to\s+verify|verification(?:\s+notes?)?|"
        r"fact-?check(?:ing)?(?:\s+notes?)?|sources?(?:\s+used)?|citations?|references?)"
        r"\*{0,2}\s*:",
        text,
        maxsplit=1,
    )[0].strip()

    raw_lines = [line.rstrip() for line in text.splitlines()]
    cleaned_lines = []
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line)
        line = re.sub(r"(?im)^\s*(?:script|narration|voiceover\s+script)\s*:\s*", "", line)
        line = re.sub(r"(?im)^\s*(?:narrator|voiceover|host)\s*:\s*", "", line)

        if re.match(r"(?i)^\s*(?:here(?:’|’)s|below\s+is|let\s+me\s+know|i\s+can\s+also)\b", line):
            continue
        if re.match(r"(?i)^\s*(?:visual|b-?roll|on-?screen(?:\s+text)?|sfx|music|camera|transition|cta)\s*:", line):
            continue
        if re.match(r"(?i)^\s*(?:estimated\s+runtime|word\s+count|title\s+ideas?)\s*:", line):
            continue
        if re.match(r"^\s*\[[^\]]+\]\s*$", line):
            continue

        cleaned_lines.append(line)

    candidate = "\n".join(cleaned_lines)
    candidate = re.sub(r"\n{3,}", "\n\n", candidate).strip()
    if candidate:
        return candidate

    # Fallback if filtering was too aggressive — never discard a non-empty input.
    text = re.sub(r"(?m)^\s*[-*]\s+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _save_research_artifacts(brief_markdown: str, sources: list[Source]) -> None:
    research_dir = Path("data/projects") / active_project_id() / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    (research_dir / "research_brief.md").write_text(brief_markdown, encoding="utf-8")
    payload = [{"title": s.title, "url": s.url, "snippet": s.snippet} for s in sources]
    (research_dir / "sources.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def tab_generate_script() -> None:
    st.subheader("Generate script")

    c1, c2 = st.columns([3, 1])
    with c1:
        st.session_state.topic = st.text_input(
            "Topic",
            value=st.session_state.topic,
            placeholder="e.g., The Rise of Rome",
        )
    with c2:
        if st.button("🎲 I'm Feeling Lucky", width="stretch"):
            try:
                st.session_state.topic = generate_lucky_topic()
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                _show_openai_error(exc)
                return
            st.session_state.project_title = st.session_state.topic
            st.toast(st.session_state.topic)
            clear_downstream("script")

    st.session_state.length = st.selectbox(
        "Length",
        ["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"],
        index=["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"].index(st.session_state.length)
        if st.session_state.length in ["Short (~60 seconds)", "8–10 minutes", "20–30 minutes"]
        else 1,
    )
    st.session_state.tone = st.selectbox(
        "Tone",
        ["Documentary", "Cinematic", "Mysterious", "Playful"],
        index=["Documentary", "Cinematic", "Mysterious", "Playful"].index(st.session_state.tone)
        if st.session_state.tone in ["Documentary", "Cinematic", "Mysterious", "Playful"]
        else 0,
    )

    audience_options = [
        "General audience",
        "History enthusiasts",
        "Students (middle/high school)",
        "College learners",
        "Educators",
        "YouTube shorts viewers",
        "Custom...",
    ]
    current_audience = st.session_state.audience if st.session_state.audience in audience_options[:-1] else "Custom..."
    selected_audience = st.selectbox(
        "Audience",
        audience_options,
        index=audience_options.index(current_audience),
    )
    if selected_audience == "Custom...":
        st.session_state.audience = st.text_input(
            "Custom audience",
            value=st.session_state.audience if current_audience == "Custom..." else "",
            placeholder="e.g., General audience interested in hidden history",
        ).strip() or "General audience"
    else:
        st.session_state.audience = selected_audience

    angle_options = [
        "Balanced overview",
        "Rise and fall arc",
        "Turning points",
        "Unsung figures",
        "Causes and consequences",
        "Myth vs reality",
        "Lessons for today",
        "Custom...",
    ]
    current_angle = st.session_state.story_angle if st.session_state.story_angle in angle_options[:-1] else "Custom..."
    selected_angle = st.selectbox(
        "Angle",
        angle_options,
        index=angle_options.index(current_angle),
    )
    if selected_angle == "Custom...":
        st.session_state.story_angle = st.text_input(
            "Custom angle",
            value=st.session_state.story_angle if current_angle == "Custom..." else "",
            placeholder="e.g., Focus on causes and long-term consequences",
        ).strip() or "Balanced overview"
    else:
        st.session_state.story_angle = selected_angle
    st.session_state.use_web_research = st.checkbox(
        "Use web research",
        value=bool(st.session_state.use_web_research),
        help="When enabled, gathers 3-8 web sources and adds citations [1], [2], ... to the brief.",
    )

    if st.button("Generate Research Brief", width="stretch"):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic before generating a brief.")
            return

        web_sources: list[Source] = []
        if st.session_state.use_web_research:
            try:
                web_sources = search_topic(st.session_state.topic, max_results=6)
            except Exception:
                web_sources = []

        if st.session_state.use_web_research and web_sources:
            st.session_state.research_brief_text = summarize_sources(st.session_state.topic, web_sources)
            st.toast(f"Research brief generated with {len(web_sources)} web source(s).")
        else:
            if st.session_state.use_web_research:
                st.info("Web research unavailable right now, using LLM-only brief fallback.")
            with st.spinner("Generating research brief..."):
                try:
                    st.session_state.research_brief_text = generate_research_brief(
                        topic=st.session_state.topic,
                        tone=st.session_state.tone,
                        length=st.session_state.length,
                        audience=st.session_state.audience,
                        angle=st.session_state.story_angle,
                    )
                except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                    _show_openai_error(exc)
                    return
            st.toast("Research brief generated.")

        st.session_state.research_sources = [
            {"title": source.title, "url": source.url, "snippet": source.snippet} for source in web_sources
        ]
        _save_research_artifacts(st.session_state.research_brief_text, web_sources)

    if st.session_state.research_brief_text.strip():
        with st.expander("Research brief", expanded=False):
            st.markdown(st.session_state.research_brief_text)
            st.checkbox(
                "Use this brief to generate script",
                key="use_research_brief_for_script",
            )

    st.session_state.reading_level = st.selectbox(
        "Reading level",
        ["General", "Middle School", "High School", "College"],
        index=["General", "Middle School", "High School", "College"].index(st.session_state.reading_level)
        if st.session_state.reading_level in ["General", "Middle School", "High School", "College"]
        else 0,
    )
    st.session_state.pacing = st.selectbox(
        "Pacing",
        ["Balanced", "Fast", "Slow and reflective"],
        index=["Balanced", "Fast", "Slow and reflective"].index(st.session_state.pacing)
        if st.session_state.pacing in ["Balanced", "Fast", "Slow and reflective"]
        else 0,
    )

    st.markdown("**Refinement passes**")
    st.session_state.run_clarity_pass = st.checkbox(
        "Consistency + clarity pass",
        value=bool(st.session_state.run_clarity_pass),
        help="Checks setup/payoff continuity and improves clarity.",
    )
    st.session_state.run_retention_pass = st.checkbox(
        "Retention pass",
        value=bool(st.session_state.run_retention_pass),
        help="Tightens pacing and reduces filler.",
    )
    st.session_state.run_safety_pass = st.checkbox(
        "Safety / claims pass",
        value=bool(st.session_state.run_safety_pass),
        help="Flags uncertain claims and appends verification notes.",
    )

    if st.button("Generate Outline", width="stretch"):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic before generating an outline.")
            return
        with st.spinner("Generating outline..."):
            try:
                outline_payload = generate_outline(
                    topic=st.session_state.topic,
                    research_brief=st.session_state.research_brief_text,
                    tone=st.session_state.tone,
                    length=st.session_state.length,
                    audience=st.session_state.audience,
                    angle=st.session_state.story_angle,
                )
            except Exception as exc:  # noqa: BLE001
                _show_openai_error(exc)
                return
        st.session_state.outline_json_text = json.dumps(outline_payload, indent=2)
        _save_outline_json(st.session_state.outline_json_text)
        st.toast("Outline generated.")

    st.text_area(
        "Outline (editable JSON)",
        value=st.session_state.outline_json_text,
        height=260,
        key="outline_json_text",
        help="Edit hook/context/beats/twist/modern relevance/CTA before generating script.",
    )

    if st.button("Generate Script from Outline", width="stretch"):
        if not st.session_state.outline_json_text.strip():
            st.warning("Generate or paste an outline first.")
            return
        try:
            outline_payload = json.loads(st.session_state.outline_json_text)
        except json.JSONDecodeError:
            st.warning("Outline must be valid JSON before script generation.")
            return
        _save_outline_json(st.session_state.outline_json_text)
        desired = int(st.session_state.get("max_scenes", 8) or 8)
        with st.spinner("Generating script from outline..."):
            try:
                generated_script = generate_script_from_outline(
                    outline=outline_payload,
                    tone=st.session_state.tone,
                    reading_level=st.session_state.reading_level,
                    pacing=st.session_state.pacing,
                    desired_scenes=desired,
                )
                generated_script = _clean_generated_script(_apply_refinement_passes(generated_script))
            except Exception as exc:  # noqa: BLE001
                _show_openai_error(exc)
                return
        st.session_state.script_text = generated_script
        st.session_state.generated_script_text_input = generated_script
        st.session_state.pending_script_text_input = generated_script
        st.session_state.project_title = st.session_state.topic or st.session_state.project_title
        clear_downstream("script")
        save_project_state(active_project_id())
        _save_script_to_supabase(active_project_id(), generated_script)
        st.toast("Script generated from outline.")
        st.rerun()

    if st.button("Generate Script", type="primary", width="stretch"):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic or use I'm Feeling Lucky.")
            return
        brief_for_script = st.session_state.research_brief_text if st.session_state.use_research_brief_for_script else ""
        desired = int(st.session_state.get("max_scenes", 8) or 8)
        with st.spinner("Generating script..."):
            try:
                generated_script = generate_script(
                    topic=st.session_state.topic,
                    length=st.session_state.length,
                    tone=st.session_state.tone,
                    audience=st.session_state.audience,
                    angle=st.session_state.story_angle,
                    research_brief=brief_for_script,
                    desired_scenes=desired,
                )
                generated_script = _clean_generated_script(_apply_refinement_passes(generated_script))
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                _show_openai_error(exc)
                return
        st.session_state.script_text = generated_script
        st.session_state.generated_script_text_input = generated_script
        st.session_state.pending_script_text_input = generated_script
        st.session_state.project_title = st.session_state.topic or st.session_state.project_title
        clear_downstream("script")
        save_project_state(active_project_id())
        _save_script_to_supabase(active_project_id(), generated_script)
        st.toast("Script generated.")
        st.rerun()

    if script_ready():
        # Flush any pending script update into the textarea's session-state key
        # *before* the widget is rendered so Streamlit displays the new value.
        # We use a dedicated boolean flag (_script_update_pending) instead of
        # relying on pending_script_text_input being truthy — an empty string is
        # falsy and would silently swallow a valid update to an empty script.
        if st.session_state.get("_script_update_pending"):
            st.session_state.generated_script_text_input = st.session_state.pending_script_text_input
            st.session_state.pending_script_text_input = ""
            st.session_state._script_update_pending = False
        elif st.session_state.pending_script_text_input:
            # Legacy path: handle any pending value set before the flag existed.
            st.session_state.generated_script_text_input = st.session_state.pending_script_text_input
            st.session_state.pending_script_text_input = ""

        with st.expander("Script (editable)", expanded=True):
            st.text_area(
                "Script",
                key="generated_script_text_input",
                height=320,
                help="Edit the generated script directly. Only narration/script text should be kept here.",
            )
            if st.button("Save edited script", width="stretch"):
                raw = st.session_state.generated_script_text_input or ""
                # Light cleaning only — the user just typed this, no SCENE markers.
                cleaned_script = _clean_generated_script(raw, light=True)
                # Absolute fallback: never discard the user's edits.
                if not cleaned_script:
                    cleaned_script = raw.strip()
                st.session_state.script_text = cleaned_script
                st.session_state.pending_script_text_input = cleaned_script
                st.session_state._script_update_pending = True
                clear_downstream("script")
                save_project_state(active_project_id())
                _save_script_to_supabase(active_project_id(), cleaned_script)
                st.toast("Script updated.")
                st.rerun()

            st.divider()
            st.session_state.setdefault("script_edit_direction", "")
            st.text_input(
                "Direction for script edit",
                key="script_edit_direction",
                placeholder="e.g. make it shorter, add more humor, simplify for younger audiences",
                help="Describe how you want the script revised, then click Apply Direction.",
            )
            if st.button("Apply Direction", width="stretch"):
                direction = st.session_state.script_edit_direction.strip()
                if not direction:
                    st.warning("Enter a direction before applying.")
                else:
                    current_script = st.session_state.generated_script_text_input
                    with st.spinner("Applying direction..."):
                        try:
                            revised = edit_script_with_direction(current_script, direction)
                        except Exception as exc:
                            _show_openai_error(exc)
                            revised = None
                    if revised:
                        # Use light cleaning: the input was already a clean
                        # plain-text script; aggressive cleaning may strip valid
                        # narration returned by the LLM.
                        cleaned = _clean_generated_script(revised, light=True)
                        # Absolute fallback: never lose the LLM's revision.
                        if not cleaned:
                            cleaned = revised.strip()
                        st.session_state.script_text = cleaned
                        st.session_state.pending_script_text_input = cleaned
                        st.session_state._script_update_pending = True
                        clear_downstream("script")
                        save_project_state(active_project_id())
                        _save_script_to_supabase(active_project_id(), cleaned)
                        st.toast("Script updated with direction.")
                        st.rerun()

        with st.expander("Splitter debug", expanded=False):
            splitter_debug = st.checkbox(
                "Show script-to-scenes debug stats",
                value=False,
                key="generate_script_splitter_debug",
            )
            if splitter_debug:
                target_scenes = int(st.session_state.get("max_scenes", 8) or 8)
                scene_texts, debug = split_script_into_scene_strings(
                    st.session_state.generated_script_text_input,
                    target_scenes=target_scenes,
                    return_debug=True,
                )
                st.write(f"len(scene_texts): {len(scene_texts)}")
                st.write(f"word counts per scene: {debug.get('word_counts', [])}")
                previews = [f"{idx + 1:02d}: {txt[:80]}" for idx, txt in enumerate(scene_texts)]
                st.code("\n".join(previews) if previews else "(no scenes)")

