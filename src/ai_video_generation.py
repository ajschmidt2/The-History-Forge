"""AI video generation service — Google Veo and OpenAI Sora.

Both providers follow an async / long-running-operation pattern:
  1. Submit a generation job.
  2. Poll until the job is done (typically 30 – 120 s).
  3. Download the resulting video bytes.
  4. Upload to the ``generated-videos`` Supabase bucket.
  5. Record the asset in the ``assets`` table.

Public API
----------
  generate_video(prompt, provider, project_id) -> str
      Returns the public Supabase URL of the generated video, or raises on
      any unrecoverable error.

  veo_configured()   -> bool
  sora_configured()  -> bool
      Credential-check helpers used by the UI to disable unavailable providers.
"""
from __future__ import annotations

import base64
import io
import time
import uuid
from typing import Optional

import requests

import src.supabase_storage as _sb_store
from src.config import get_secret

# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

_VEO_PLACEHOLDER = {"PASTE_PROJECT_ID_HERE", "PASTE_ACCESS_TOKEN_HERE", ""}


def veo_configured() -> bool:
    """Return True when all three Veo / Vertex AI credentials are present."""
    project_id = get_secret("GOOGLE_CLOUD_PROJECT_ID")
    token = get_secret("GOOGLE_ACCESS_TOKEN")
    return bool(project_id) and project_id not in _VEO_PLACEHOLDER \
        and bool(token) and token not in _VEO_PLACEHOLDER


def sora_configured() -> bool:
    """Return True when an OpenAI API key is available."""
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    return bool(key) and not key.startswith("PASTE")


# ---------------------------------------------------------------------------
# Internal: Google Veo via Vertex AI
# ---------------------------------------------------------------------------

_VEO_MODEL = "veo-2.0-generate-001"
_POLL_INTERVAL_S = 8   # seconds between status polls
_MAX_POLLS = 90        # up to 12 minutes total


def _veo_headers() -> dict[str, str]:
    token = get_secret("GOOGLE_ACCESS_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _generate_veo(prompt: str) -> bytes:
    """Submit a Veo job and block until the video is ready.  Returns MP4 bytes."""
    project_id = get_secret("GOOGLE_CLOUD_PROJECT_ID")
    location = get_secret("GOOGLE_CLOUD_LOCATION") or "us-central1"

    if not project_id or not get_secret("GOOGLE_ACCESS_TOKEN"):
        raise ValueError(
            "Google Veo credentials are not configured.  "
            "Set GOOGLE_CLOUD_PROJECT_ID and GOOGLE_ACCESS_TOKEN in secrets.toml."
        )

    submit_url = (
        f"https://{location}-aiplatform.googleapis.com/v1beta1"
        f"/projects/{project_id}/locations/{location}"
        f"/publishers/google/models/{_VEO_MODEL}:predictLongRunning"
    )
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "aspectRatio": "16:9",
            "videoDurationSeconds": 8,
            "sampleCount": 1,
        },
    }

    resp = requests.post(submit_url, json=payload, headers=_veo_headers(), timeout=60)
    if resp.status_code == 401:
        raise PermissionError(
            "Vertex AI returned 401 Unauthorized.  "
            "Your GOOGLE_ACCESS_TOKEN may have expired — regenerate it with "
            "`gcloud auth print-access-token`."
        )
    resp.raise_for_status()

    operation_name = resp.json().get("name")
    if not operation_name:
        raise RuntimeError(f"Veo did not return an operation name.  Response: {resp.text[:500]}")

    # Poll the long-running operation
    op_url = (
        f"https://{location}-aiplatform.googleapis.com/v1beta1/{operation_name}"
    )

    for attempt in range(_MAX_POLLS):
        time.sleep(_POLL_INTERVAL_S)
        op_resp = requests.get(op_url, headers=_veo_headers(), timeout=30)
        op_resp.raise_for_status()
        op_data = op_resp.json()

        if not op_data.get("done"):
            continue  # Still running

        error = op_data.get("error")
        if error:
            raise RuntimeError(
                f"Veo generation failed: {error.get('message', op_data)}"
            )

        predictions = op_data.get("response", {}).get("predictions", [])
        if not predictions:
            raise RuntimeError("Veo reported done but returned no predictions.")

        pred = predictions[0]

        # Case 1: inline base-64 encoded video
        b64 = pred.get("bytesBase64Encoded") or pred.get("videoData")
        if b64:
            return base64.b64decode(b64)

        # Case 2: GCS URI — download via signed URL in the prediction
        video_url = pred.get("videoUrl") or pred.get("gcsUri")
        if video_url and video_url.startswith("http"):
            dl = requests.get(video_url, timeout=120)
            dl.raise_for_status()
            return dl.content

        raise RuntimeError(
            f"Veo returned an unexpected prediction format: {str(pred)[:300]}"
        )

    raise TimeoutError(
        f"Veo generation did not complete within {_MAX_POLLS * _POLL_INTERVAL_S} seconds."
    )


# ---------------------------------------------------------------------------
# Internal: OpenAI Sora
# ---------------------------------------------------------------------------

_SORA_SUBMIT_URL = "https://api.openai.com/v1/video/generations"


def _sora_headers() -> dict[str, str]:
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _generate_sora(prompt: str) -> bytes:
    """Submit a Sora job and block until the video is ready.  Returns MP4 bytes."""
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    if not key:
        raise ValueError(
            "OpenAI API key is not configured.  "
            "Set openai_api_key in secrets.toml."
        )

    # Submit the generation job
    payload = {
        "model": "sora",
        "prompt": prompt,
        "n": 1,
        "size": "1280x720",
    }
    resp = requests.post(_SORA_SUBMIT_URL, json=payload, headers=_sora_headers(), timeout=60)
    if resp.status_code == 401:
        raise PermissionError(
            "OpenAI returned 401 Unauthorized.  Check your openai_api_key."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "OpenAI returned 404 — the Sora video generation endpoint may not be "
            "available on your account tier yet.  Verify access at platform.openai.com."
        )
    resp.raise_for_status()

    job = resp.json()
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError(f"Sora did not return a job ID.  Response: {resp.text[:500]}")

    # Poll until complete
    status_url = f"{_SORA_SUBMIT_URL}/{job_id}"

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

def generate_video(prompt: str, provider: str, project_id: str) -> str:
    """Generate a video from *prompt* using *provider* and return its public URL.

    Parameters
    ----------
    prompt:
        The text description of the video to generate.
    provider:
        Either ``"veo"`` or ``"sora"`` (case-insensitive).
    project_id:
        The active History Forge project ID, used as a storage path prefix in
        Supabase and as the foreign key when recording the asset.

    Returns
    -------
    str
        Public URL of the uploaded video in the ``generated-videos`` Supabase bucket.

    Raises
    ------
    ValueError
        If *provider* is unrecognised or if the required credentials are missing.
    RuntimeError / TimeoutError
        If the provider API call fails or times out.
    """
    provider = (provider or "").strip().lower()

    if provider == "veo":
        video_bytes = _generate_veo(prompt)
    elif provider == "sora":
        video_bytes = _generate_sora(prompt)
    else:
        raise ValueError(f"Unknown video provider '{provider}'.  Use 'veo' or 'sora'.")

    # Build a unique filename that embeds provider and a short ID
    short_id = uuid.uuid4().hex[:8]
    filename = f"{provider}_{short_id}.mp4"

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
        return f"data:video/mp4;base64,{b64}"

    return url
