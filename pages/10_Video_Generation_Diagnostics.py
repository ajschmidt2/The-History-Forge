from __future__ import annotations

"""Diagnostics for Gemini Developer API video generation and fal.ai fallback."""

from pathlib import Path
import traceback

import streamlit as st

from app import require_passcode
from src.config import get_secret
from src.constants import SUPABASE_VIDEO_BUCKET
from src.providers.gemini_provider import get_video_model
from src.services.fal_video_test import get_fal_key_status, run_fal_video_test
from src.services.google_veo_video import DEFAULT_GOOGLE_VIDEO_MODEL, generate_google_veo_lite_video, get_gemini_api_key

st.set_page_config(page_title="Video Generation Diagnostics", page_icon="🎬")
require_passcode()
st.title("🎬 Video Generation Diagnostics")
st.caption("Diagnose Gemini Developer API Veo generation and the optional fal.ai fallback.")


with st.expander("Gemini Developer API Setup", expanded=True):
    if get_gemini_api_key():
        st.success("GEMINI_API_KEY detected.")
    else:
        st.error("GEMINI_API_KEY is missing. Create a key in Google AI Studio and add it to Streamlit secrets or your local environment.")
    st.markdown(
        "- Current video model: `%s`\n"
        "- Configure with `GEMINI_VIDEO_MODEL` or `HF_GOOGLE_VIDEO_MODEL`.\n"
        "- Legacy Vertex settings are not used for this app path: `GOOGLE_APPLICATION_CREDENTIALS`, service-account JSON, project, and location."
        % get_video_model()
    )


with st.expander("Run Gemini Veo Lite Video Test", expanded=False):
    st.caption("This calls the Google GenAI SDK with GEMINI_API_KEY. It may incur Gemini/Veo usage charges.")
    google_model = st.text_input(
        "Google model id",
        value=str(get_secret("GEMINI_VIDEO_MODEL", get_secret("HF_GOOGLE_VIDEO_MODEL", DEFAULT_GOOGLE_VIDEO_MODEL)) or DEFAULT_GOOGLE_VIDEO_MODEL),
        key="google_veo_test_model",
    )
    google_prompt = st.text_area(
        "Prompt",
        value="A cinematic handheld documentary shot of a restored medieval market at dusk, natural motion and ambient crowd audio.",
        key="google_veo_test_prompt",
        height=100,
    )
    google_image_file = st.file_uploader(
        "Input image",
        type=["png", "jpg", "jpeg", "webp"],
        key="google_veo_test_image_upload",
        help="Upload a reference image for image-to-video.",
    )
    google_local_image = st.text_input(
        "Or local image path",
        value="",
        key="google_veo_test_image_local",
    )
    google_aspect_ratio = st.selectbox("Aspect ratio", options=["9:16", "16:9", "1:1"], index=0)
    google_output_path = st.text_input(
        "Output path",
        value="data/google_veo_video_tests/google_veo_test.mp4",
        key="google_veo_test_output_path",
    )

    if st.button("Run Gemini Veo Lite Video Test", key="run_google_veo_lite_video_test_btn"):
        image_source = google_image_file or google_local_image.strip()
        if not image_source:
            st.error("Please upload an image or provide a local image path.")
        elif not google_prompt.strip():
            st.error("Prompt cannot be empty.")
        else:
            try:
                with st.spinner("Generating video with Gemini Developer API..."):
                    result = generate_google_veo_lite_video(
                        prompt=google_prompt,
                        image_source=image_source,
                        aspect_ratio=google_aspect_ratio,
                        duration_seconds=5,
                        output_path=google_output_path,
                        debug_dir=Path("data/google_veo_video_tests/debug"),
                        model=google_model,
                    )
                st.json(result)
                if result.get("ok") and Path(google_output_path).exists():
                    st.success("Video generated and saved successfully.")
                    st.video(google_output_path)
                else:
                    st.error(f"Test failed: {result.get('error', 'unknown error')}")
            except Exception:
                st.error("Unexpected Gemini video test error:")
                st.code(traceback.format_exc())


with st.expander("fal.ai Video Test", expanded=False):
    fal_status = get_fal_key_status()
    if fal_status.get("ok"):
        st.success(f"fal key detected (prefix: {fal_status.get('key_prefix', '')}..., length: {fal_status.get('key_length', 0)}).")
    else:
        st.warning(f"fal key not available: {fal_status.get('error', 'unknown error')}")

    fal_model = st.text_input("Model endpoint", value="fal-ai/wan/v2.2-5b/image-to-video")
    fal_prompt = st.text_area("Prompt", value="A cinematic slow dolly shot through drifting fog, dramatic lighting.", height=100)
    fal_image_file = st.file_uploader("Input image", type=["png", "jpg", "jpeg", "webp"], key="fal_video_test_image")
    fal_duration = st.number_input("Duration", min_value=1, max_value=16, value=5, step=1)
    fal_aspect_ratio = st.text_input("Aspect ratio", value="16:9")

    if st.button("Run fal.ai Video Test", key="run_fal_video_test_btn"):
        if not fal_image_file:
            st.error("Please upload an image before running the fal.ai video test.")
        elif not fal_prompt.strip():
            st.error("Prompt cannot be empty.")
        else:
            with st.spinner("Running fal.ai test..."):
                fal_result = run_fal_video_test(
                    model=fal_model,
                    prompt=fal_prompt,
                    image_source=fal_image_file,
                    duration=int(fal_duration),
                    aspect_ratio=fal_aspect_ratio.strip() or None,
                )
            st.json(fal_result)
            if fal_result.get("ok") and fal_result.get("output_path"):
                st.video(fal_result["output_path"])


st.header(f"Supabase Storage - `{SUPABASE_VIDEO_BUCKET}` Bucket")
st.caption("Supabase storage is still used for app assets; it is no longer required to proxy Google video generation.")
if st.button(f"Check {SUPABASE_VIDEO_BUCKET} Bucket", key="bucket_run"):
    try:
        from supabase import create_client

        supabase_url = str(get_secret("SUPABASE_URL", "") or "").strip()
        supabase_key = str(get_secret("SUPABASE_KEY", "") or get_secret("SUPABASE_ANON_KEY", "") or get_secret("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
        if not supabase_url:
            st.error("SUPABASE_URL is not configured.")
        elif not supabase_key:
            st.error("Supabase key is not configured.")
        else:
            sb = create_client(supabase_url, supabase_key)
            sb.storage.from_(SUPABASE_VIDEO_BUCKET).list()
            st.success(f"Bucket `{SUPABASE_VIDEO_BUCKET}` exists and is accessible.")
    except Exception as exc:
        st.error(f"Bucket `{SUPABASE_VIDEO_BUCKET}` is not accessible: `{exc}`")
