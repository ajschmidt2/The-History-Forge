"""AI video generation service — Google Veo and OpenAI Sora.

Both providers follow an async pattern:
  • Veo is proxied through a Supabase Edge Function (server-side Vertex AI call).
  • Sora is called directly from this backend module.
  • The resulting video bytes are optionally saved locally, uploaded to the
    ``generated-videos`` Supabase bucket, and recorded in the ``assets`` table.

Public API
----------
  generate_video(prompt, provider, project_id, aspect_ratio, save_dir) -> (str, str | None)
      Returns a tuple of (public_url, local_path).  local_path is None when
      save_dir is not supplied or the write fails.

  veo_configured()   -> bool
  sora_configured()  -> bool
      Credential-check helpers used by the UI to disable unavailable providers.

  VEO_ASPECT_RATIOS   — supported aspect ratios for Veo
  SORA_ASPECT_RATIOS  — supported aspect ratios for Sora
"""
from __future__ import annotations

import base64
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

import src.supabase_storage as _sb_store
from src.config import get_secret

# ---------------------------------------------------------------------------
# Aspect-ratio constants exposed to the UI
# ---------------------------------------------------------------------------

VEO_ASPECT_RATIOS: list[str] = ["16:9", "9:16", "1:1"]
"""Aspect ratios supported by Google Veo."""

SORA_ASPECT_RATIOS: list[str] = ["16:9", "9:16", "1:1"]
"""Aspect ratios supported by OpenAI Sora."""

# Map the shared aspect-ratio labels to the exact Sora ``size`` parameter values.
_SORA_SIZE_MAP: dict[str, str] = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "1:1":  "1080x1080",
}

# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_URLS = {"", "https://xxxxxxxxxxxx.supabase.co"}
_PLACEHOLDER_KEYS = {"", "your-anon-public-key", "your-anon-key-here"}


def _supabase_invoke_key() -> str:
    """Return the best available Supabase key for invoking Edge Functions."""
    return (
        get_secret("SUPABASE_KEY")
        or get_secret("SUPABASE_ANON_KEY")
        or get_secret("SUPABASE_SERVICE_ROLE_KEY")
    )


def veo_configured() -> bool:
    """Return True when the frontend can invoke the Veo Supabase Edge Function."""
    url = get_secret("SUPABASE_URL")
    key = _supabase_invoke_key()
    return bool(url) and url not in _PLACEHOLDER_URLS and bool(key) and key not in _PLACEHOLDER_KEYS


def sora_configured() -> bool:
    """Return True when an OpenAI API key is available."""
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    return bool(key) and not key.startswith("PASTE")


# ---------------------------------------------------------------------------
# Internal: Google Veo via Supabase Edge Function
# ---------------------------------------------------------------------------

_POLL_INTERVAL_S = 8   # seconds between status polls
_MAX_POLLS = 90        # up to 12 minutes total


def _generate_veo(prompt: str, aspect_ratio: str = "16:9") -> bytes:
    """Generate Veo video bytes by invoking a Supabase Edge Function."""
    supabase_url = get_secret("SUPABASE_URL")
    supabase_key = _supabase_invoke_key()
    function_name = get_secret("SUPABASE_VEO_FUNCTION_NAME", "veo-generate")

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Veo is not configured. Set SUPABASE_URL and one of "
            "SUPABASE_KEY / SUPABASE_ANON_KEY so the app can call the Supabase Edge Function."
        )

    veo_ratio = aspect_ratio if aspect_ratio in VEO_ASPECT_RATIOS else "16:9"
    invoke_url = f"{supabase_url.rstrip('/')}/functions/v1/{function_name}"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "application/json",
    }
    payload = {"prompt": prompt, "aspectRatio": veo_ratio}

    resp = requests.post(invoke_url, json=payload, headers=headers, timeout=300)
    if resp.status_code == 401:
        raise PermissionError(
            "Supabase Edge Function returned 401 Unauthorized.\n\n"
            "Most common fix: redeploy the function with JWT verification disabled:\n"
            "  supabase functions deploy veo-generate --no-verify-jwt\n\n"
            "Also confirm that SUPABASE_KEY in .streamlit/secrets.toml is set to "
            "your real anon/public key (Project Settings → API in the Supabase "
            "dashboard), not a placeholder value."
        )
    resp.raise_for_status()

    body = resp.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"]))

    b64 = body.get("videoBase64")
    if not b64:
        raise RuntimeError("Veo Edge Function returned no videoBase64 payload.")

    return base64.b64decode(b64)


# ---------------------------------------------------------------------------
# Internal: OpenAI Sora
# ---------------------------------------------------------------------------

_SORA_SUBMIT_URLS = (
    "https://api.openai.com/v1/videos",
    # Legacy endpoint path kept as a fallback for compatibility.
    "https://api.openai.com/v1/video/generations",
)


def _sora_headers() -> dict[str, str]:
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _generate_sora(prompt: str, aspect_ratio: str = "16:9") -> bytes:
    """Submit a Sora job and block until the video is ready.  Returns MP4 bytes."""
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    if not key:
        raise ValueError(
            "OpenAI API key is not configured.  "
            "Set openai_api_key in secrets.toml."
        )

    # Map the shared aspect-ratio label to the Sora ``size`` parameter.
    sora_size = _SORA_SIZE_MAP.get(aspect_ratio, _SORA_SIZE_MAP["16:9"])

    # Submit the generation job
    payload = {
        "model": "sora",
        "prompt": prompt,
        "n": 1,
        "size": sora_size,
    }
    resp = None
    submit_url = None
    for candidate_url in _SORA_SUBMIT_URLS:
        candidate_resp = requests.post(
            candidate_url, json=payload, headers=_sora_headers(), timeout=60
        )
        if candidate_resp.status_code == 401:
            raise PermissionError(
                "OpenAI returned 401 Unauthorized.  Check your openai_api_key."
            )
        if candidate_resp.status_code != 404:
            resp = candidate_resp
            submit_url = candidate_url
            break

    if resp is None or submit_url is None:
        raise RuntimeError(
            "OpenAI returned 404 from all known Sora endpoints.  "
            "Your account may not have video access yet, or the endpoint may have changed.  "
            "Verify API video access at platform.openai.com."
        )

    resp.raise_for_status()

    job = resp.json()
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError(f"Sora did not return a job ID.  Response: {resp.text[:500]}")

    # Poll until complete
    status_url = f"{submit_url}/{job_id}"

    for attempt in range(_MAX_POLLS):
        time.sleep(_POLL_INTERVAL_S)
        status_resp = requests.get(status_url, headers=_sora_headers(), timeout=30)
        status_resp.raise_for_status()
        status_data = status_resp.json()

        status = status_data.get("status", "")

        if status in {"failed", "cancelled"}:
            err = status_data.get("error") or status_data.get("message", "Unknown error")
            raise RuntimeError(f"Sora generation {status}: {err}")

        if status == "completed":
            # Try to get the video URL from the response
            data = status_data.get("data", [])
            if data:
                video_url = data[0].get("url") or data[0].get("video_url")
                if video_url:
                    dl = requests.get(video_url, timeout=120)
                    dl.raise_for_status()
                    return dl.content

            # Fallback: try a content endpoint pattern
            content_url = f"{status_url}/content/video.mp4"
            dl = requests.get(content_url, headers=_sora_headers(), timeout=120)
            if dl.status_code == 200 and dl.content:
                return dl.content

            raise RuntimeError(
                "Sora reported completed but no video URL was found in the response."
            )

    raise TimeoutError(
        f"Sora generation did not complete within {_MAX_POLLS * _POLL_INTERVAL_S} seconds."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_video(
    prompt: str,
    provider: str,
    project_id: str,
    aspect_ratio: str = "16:9",
    save_dir: Optional[Path | str] = None,
) -> tuple[str, Optional[str]]:
    """Generate a video from *prompt* using *provider* and return ``(url, local_path)``.

    Parameters
    ----------
    prompt:
        The text description of the video to generate.
    provider:
        Either ``"veo"`` or ``"sora"`` (case-insensitive).
    project_id:
        The active History Forge project ID, used as a storage path prefix in
        Supabase and as the foreign key when recording the asset.
    aspect_ratio:
        Desired aspect ratio string such as ``"16:9"``, ``"9:16"``, or ``"1:1"``.
        Defaults to ``"16:9"`` when the value is unsupported by the chosen provider.
    save_dir:
        Optional directory path.  When supplied the raw MP4 bytes are written to
        ``{save_dir}/{provider}_{short_id}.mp4`` before uploading to Supabase.

    Returns
    -------
    tuple[str, str | None]
        ``(public_url, local_path)`` where *public_url* is the Supabase URL (or a
        ``data:`` URL when Supabase is not configured) and *local_path* is the
        absolute path to the locally saved file (``None`` if not saved locally).

    Raises
    ------
    ValueError
        If *provider* is unrecognised or if the required credentials are missing.
    RuntimeError / TimeoutError
        If the provider API call fails or times out.
    """
    provider = (provider or "").strip().lower()

    if provider == "veo":
        video_bytes = _generate_veo(prompt, aspect_ratio=aspect_ratio)
    elif provider == "sora":
        video_bytes = _generate_sora(prompt, aspect_ratio=aspect_ratio)
    else:
        raise ValueError(f"Unknown video provider '{provider}'.  Use 'veo' or 'sora'.")

    # Build a unique filename that embeds provider and a short ID
    short_id = uuid.uuid4().hex[:8]
    filename = f"{provider}_{short_id}.mp4"

    # Optionally save to local disk
    local_path: Optional[str] = None
    if save_dir is not None:
        try:
            save_path = Path(save_dir)
            save_path.mkdir(parents=True, exist_ok=True)
            dest = save_path / filename
            dest.write_bytes(video_bytes)
            local_path = str(dest.resolve())
        except OSError:
            local_path = None

    # Upload to Supabase and record the asset
    url = _sb_store.upload_generated_video(
        project_id=project_id,
        filename=filename,
        video_bytes=video_bytes,
        prompt=prompt,
        provider=provider,
    )

    if not url:
        # Supabase may not be configured — still return the raw bytes as a
        # data URL so the user can download, but warn them.
        b64 = base64.b64encode(video_bytes).decode()
        return f"data:video/mp4;base64,{b64}", local_path

    return url, local_path
