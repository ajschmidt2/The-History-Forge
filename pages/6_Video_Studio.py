from __future__ import annotations

import streamlit as st

from app import init_state, require_passcode, tab_video_compile


st.set_page_config(page_title="Video Studio", layout="wide")
require_passcode()
init_state()

st.info("Video Studio now lives inside the main app tab flow. This page is kept for backward compatibility.")

st.title("🎬 Video Studio")
tab_video_compile()
