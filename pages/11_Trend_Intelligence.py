from __future__ import annotations

import streamlit as st

from app import init_state, require_passcode
from src.ui.tabs.trend_intelligence import tab_trend_intelligence


st.set_page_config(page_title="Trend Intelligence", layout="wide")
require_passcode()
init_state()

st.info("Trend Intelligence is available in the main app tab flow and on this direct page.")

st.title("📈 Trend Intelligence")
tab_trend_intelligence()
