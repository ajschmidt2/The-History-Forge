import streamlit as st

from src.storage import upsert_project
from src.ui.state import active_project_id, init_state, render_project_selector, require_passcode, save_project_state
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
    require_passcode()
    init_state()
    st.title("The History Forge")
    st.caption("Generate scripts, scene lists, prompts, images, and voiceover from a single workflow.")

    render_project_selector()
    upsert_project(active_project_id(), st.session_state.project_title)

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

    save_project_state(active_project_id())


if __name__ == "__main__":
    main()
