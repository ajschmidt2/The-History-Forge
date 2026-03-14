"""
src/video/ai_video_clips.py

Generates two short AI video clips for embedding in the final render:
  - opening_clip: inserted before scene 1
  - mid_clip: inserted at the midpoint of the scene list

Prompts are derived from the first and middle image prompts in the scene list.
Provider is read from automation settings (ai_video_provider).
"""

import requests
from pathlib import Path
import streamlit as st


def _get_video_prompts(scenes: list) -> tuple[str, str]:
    """Derive opening and midpoint video prompts from existing image prompts."""
    prompts = []
    for s in scenes:
        p = getattr(s, "image_prompt", None) or getattr(s, "prompt", None) or ""
        if str(p).strip():
            prompts.append(str(p).strip())

    suffix_open = ", slow cinematic push in, dramatic lighting, documentary film style, 5 seconds"
    suffix_mid  = ", slow cinematic pan across frame, atmospheric, documentary film style, 5 seconds"

    if not prompts:
        return (
            "A dramatic cinematic establishing shot, historical setting" + suffix_open,
            "A sweeping cinematic historical scene, moody atmosphere" + suffix_mid,
        )

    mid_idx = len(prompts) // 2
    return prompts[0] + suffix_open, prompts[mid_idx] + suffix_mid


def _call_veo(prompt: str, out_path: Path) -> bool:
    """Call the Supabase veo-generate Edge Function."""
    try:
        url  = st.secrets.get("SUPABASE_URL", "")
        key  = st.secrets.get("SUPABASE_KEY", st.secrets.get("SUPABASE_ANON_KEY", ""))
        fn   = st.secrets.get("SUPABASE_VEO_FUNCTION_NAME", "veo-generate")
        if not url or not key:
            st.warning("AI Video Clips: SUPABASE_URL or SUPABASE_KEY missing.")
            return False
        resp = requests.post(
            f"{url}/functions/v1/{fn}",
            json={"prompt": prompt, "duration_seconds": 5, "aspect_ratio": "16:9"},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=180,
        )
        resp.raise_for_status()
        video_url = resp.json().get("video_url") or resp.json().get("videoUrl")
        if not video_url:
            st.warning("AI Video Clips: Veo returned no video URL.")
            return False
        out_path.write_bytes(requests.get(video_url, timeout=60).content)
        return True
    except Exception as e:
        st.warning(f"AI Video Clips (Veo) failed: {e}")
        return False


def _call_sora(prompt: str, out_path: Path) -> bool:
    """Call OpenAI Sora."""
    try:
        import openai
        api_key = st.secrets.get("OPENAI_API_KEY", st.secrets.get("openai_api_key", ""))
        if not api_key:
            st.warning("AI Video Clips: openai_api_key missing.")
            return False
        client = openai.OpenAI(api_key=api_key)
        response = client.videos.generate(
            model="sora-2",
            prompt=prompt,
            seconds=5,
        )
        video_url = response.data[0].url
        out_path.write_bytes(requests.get(video_url, timeout=60).content)
        return True
    except Exception as e:
        st.warning(f"AI Video Clips (Sora) failed: {e}")
        return False


def generate_ai_video_clips(scenes: list, tmp_dir: Path, provider: str) -> tuple:
    """
    Generate opening and midpoint AI video clips.
    Returns (opening_path | None, mid_path | None).
    """
    if not provider or provider == "None":
        return None, None

    opening_prompt, mid_prompt = _get_video_prompts(scenes)
    opening_path = tmp_dir / "ai_clip_opening.mp4"
    mid_path     = tmp_dir / "ai_clip_mid.mp4"

    st.info(f"🎬 Generating AI video clips via {provider}...")

    if provider == "Google Veo (Supabase)":
        ok1 = _call_veo(opening_prompt, opening_path)
        ok2 = _call_veo(mid_prompt, mid_path)
    elif provider == "OpenAI Sora":
        ok1 = _call_sora(opening_prompt, opening_path)
        ok2 = _call_sora(mid_prompt, mid_path)
    else:
        return None, None

    return (opening_path if ok1 and opening_path.exists() else None,
            mid_path     if ok2 and mid_path.exists()     else None)
