import json
import re
from pathlib import Path

import streamlit as st

from src.research.web_research import Source, search_topic, summarize_sources
from src.script.refine import flag_uncertain_claims, refine_for_clarity, refine_for_retention
from src.ui.state import active_project_id, clear_downstream, openai_error_message, script_ready
from utils import generate_lucky_topic, generate_outline, generate_research_brief, generate_script, generate_script_from_outline




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


def _clean_generated_script(script: str) -> str:
    text = str(script or "").strip()
    if not text:
        return ""

    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

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
        r"sources?(?:\s+used)?|citations?|references?|editor(?:'s)?\s+notes?|"
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
    cleaned_lines: list[str] = []
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line)
        line = re.sub(r"(?im)^\s*(?:script|narration|voiceover\s+script)\s*:\s*", "", line)
        line = re.sub(r"(?im)^\s*(?:narrator|voiceover|host)\s*:\s*", "", line)

        if re.match(r"(?i)^\s*(?:here(?:'|’)s|below\s+is|let\s+me\s+know|i\s+can\s+also)\b", line):
            continue
        if re.match(r"(?i)^\s*(?:scene|shot)\s*\d+\s*[:\-]", line):
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

    # Fallback if filtering was too aggressive.
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
                st.error(openai_error_message(exc))
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
                    st.error(openai_error_message(exc))
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
                st.error(openai_error_message(exc))
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
        with st.spinner("Generating script from outline..."):
            try:
                generated_script = generate_script_from_outline(
                    outline=outline_payload,
                    tone=st.session_state.tone,
                    reading_level=st.session_state.reading_level,
                    pacing=st.session_state.pacing,
                )
                generated_script = _clean_generated_script(_apply_refinement_passes(generated_script))
            except Exception as exc:  # noqa: BLE001
                st.error(openai_error_message(exc))
                return
        st.session_state.script_text = generated_script
        st.session_state.generated_script_text_input = generated_script
        st.session_state.pending_script_text_input = generated_script
        st.session_state.project_title = st.session_state.topic or st.session_state.project_title
        clear_downstream("script")
        st.toast("Script generated from outline.")
        st.rerun()

    if st.button("Generate Script", type="primary", width="stretch"):
        if not st.session_state.topic.strip():
            st.warning("Enter a topic or use I'm Feeling Lucky.")
            return
        brief_for_script = st.session_state.research_brief_text if st.session_state.use_research_brief_for_script else ""
        with st.spinner("Generating script..."):
            try:
                generated_script = generate_script(
                    topic=st.session_state.topic,
                    length=st.session_state.length,
                    tone=st.session_state.tone,
                    audience=st.session_state.audience,
                    angle=st.session_state.story_angle,
                    research_brief=brief_for_script,
                )
                generated_script = _clean_generated_script(_apply_refinement_passes(generated_script))
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                st.error(openai_error_message(exc))
                return
        st.session_state.script_text = generated_script
        st.session_state.generated_script_text_input = generated_script
        st.session_state.pending_script_text_input = generated_script
        st.session_state.project_title = st.session_state.topic or st.session_state.project_title
        clear_downstream("script")
        st.toast("Script generated.")
        st.rerun()

    if script_ready():
        if st.session_state.pending_script_text_input:
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
                cleaned_script = _clean_generated_script(st.session_state.generated_script_text_input)
                st.session_state.script_text = cleaned_script
                st.session_state.generated_script_text_input = cleaned_script
                st.session_state.pending_script_text_input = cleaned_script
                clear_downstream("script")
                st.toast("Script updated.")
                st.rerun()
