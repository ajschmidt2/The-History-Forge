import json
from pathlib import Path

import streamlit as st

from src.research.web_research import Source, search_topic, summarize_sources
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

    st.session_state.audience = st.text_input(
        "Audience",
        value=st.session_state.audience,
        placeholder="e.g., General audience interested in hidden history",
    )
    st.session_state.story_angle = st.text_input(
        "Angle",
        value=st.session_state.story_angle,
        placeholder="e.g., Focus on causes and long-term consequences",
    )
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
            except Exception as exc:  # noqa: BLE001
                st.error(openai_error_message(exc))
                return
        st.session_state.script_text = generated_script
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
            except Exception as exc:  # noqa: BLE001 - surface OpenAI errors to user
                st.error(openai_error_message(exc))
                return
        st.session_state.script_text = generated_script
        st.session_state.pending_script_text_input = generated_script
        st.session_state.project_title = st.session_state.topic or st.session_state.project_title
        clear_downstream("script")
        st.toast("Script generated.")
        st.rerun()

    if script_ready():
        with st.expander("Preview script", expanded=False):
            st.write(st.session_state.script_text)
