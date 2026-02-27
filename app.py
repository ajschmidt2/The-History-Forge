import streamlit as st

from image_gen import validate_gemini_api_key
from src.storage import upsert_project
import src.supabase_storage as _sb_store
from src.ui.state import active_project_id, init_state, render_project_selector, require_passcode, save_project_state
from src.ui.tabs.ai_video_generator import tab_ai_video_generator
from src.ui.tabs.export import tab_export
from src.ui.tabs.generate_script import tab_generate_script
from src.ui.tabs.images import tab_create_images
from src.ui.tabs.paste_script import tab_paste_script
from src.ui.tabs.prompts import tab_create_prompts
from src.ui.tabs.scenes import tab_create_scenes
from src.ui.tabs.thumbnail import tab_thumbnail_title
from src.ui.tabs.video_studio import tab_video_compile
from src.ui.tabs.voiceover import tab_voiceover


def main() -> None:
    st.set_page_config(page_title="The History Forge", layout="wide")
    # Allow the app shell to load even when image generation credentials
    # are missing; image generation paths still validate strictly when used.
    validate_gemini_api_key(required=False)
    require_passcode()
    init_state()
    st.title("The History Forge")
    st.caption("Generate scripts, scene lists, prompts, images, and voiceover from a single workflow.")

    render_project_selector()
    upsert_project(active_project_id(), st.session_state.project_title)
    _sb_store.upsert_project(active_project_id(), st.session_state.project_title)

    tabs = st.tabs(
        [
            "📝 Paste Script",
            "✨ Generate Script",
            "🧩 Scenes",
            "🧠 Prompts",
            "🖼️ Images",
            "🎙️ Voiceover",
            "📦 Export",
            "🎬 Video Studio",
            "🖼️ Title + Thumbnail",
            "🎥 AI Video Generator",
        ]
    )

    with tabs[0]:
        tab_paste_script()
    with tabs[1]:
        tab_generate_script()
    with tabs[2]:
        tab_create_scenes()
    with tabs[3]:
        tab_create_prompts()
    with tabs[4]:
        tab_create_images()
    with tabs[5]:
        tab_voiceover()
    with tabs[6]:
        tab_export()
    with tabs[7]:
        tab_video_compile()
    with tabs[8]:
        tab_thumbnail_title()
    with tabs[9]:
        tab_ai_video_generator()

    save_project_state(active_project_id())


if __name__ == "__main__":
    main()
