"""AI video generation service — Google Veo and OpenAI Sora.

Both providers follow an async / long-running-operation pattern:
  1. Submit a generation job.
  2. Poll until the job is done (typically 30 – 120 s).
  3. Download the resulting video bytes.
  4. Optionally save to a local directory.
  5. Upload to the ``generated-videos`` Supabase bucket.
  6. Record the asset in the ``assets`` table.

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
import io
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

SORA_ASPECT_RATIOS: list[str] = ["16:9", "9:16", "16:9 (HD)", "9:16 (HD)"]
"""Aspect ratios supported by OpenAI Sora.

Maps to the ``size`` parameter accepted by ``POST /v1/videos``:
  16:9       → 1280x720
  9:16       → 720x1280
  16:9 (HD)  → 1792x1024
  9:16 (HD)  → 1024x1792
"""

# Map the shared aspect-ratio labels to the exact Sora ``size`` parameter values.
# Valid Sora sizes: "720x1280", "1280x720", "1024x1792", "1792x1024".
_SORA_SIZE_MAP: dict[str, str] = {
    "16:9":       "1280x720",
    "9:16":       "720x1280",
    "16:9 (HD)":  "1792x1024",
    "9:16 (HD)":  "1024x1792",
}

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


def _generate_veo(prompt: str, aspect_ratio: str = "16:9") -> bytes:
    """Submit a Veo job and block until the video is ready.  Returns MP4 bytes."""
    project_id = get_secret("GOOGLE_CLOUD_PROJECT_ID")
    location = get_secret("GOOGLE_CLOUD_LOCATION") or "us-central1"

    if not project_id or not get_secret("GOOGLE_ACCESS_TOKEN"):
        raise ValueError(
            "Google Veo credentials are not configured.  "
            "Set GOOGLE_CLOUD_PROJECT_ID and GOOGLE_ACCESS_TOKEN in secrets.toml."
        )

    # Normalise to a value Veo actually accepts; fall back to 16:9.
    veo_ratio = aspect_ratio if aspect_ratio in VEO_ASPECT_RATIOS else "16:9"

    submit_url = (
        f"https://{location}-aiplatform.googleapis.com/v1beta1"
        f"/projects/{project_id}/locations/{location}"
        f"/publishers/google/models/{_VEO_MODEL}:predictLongRunning"
    )
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "aspectRatio": veo_ratio,
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
# Internal: OpenAI Sora  (POST /v1/videos  →  GET /v1/videos/{id}  →  GET /v1/videos/{id}/content)
# ---------------------------------------------------------------------------

_SORA_BASE_URL = "https://api.openai.com/v1/videos"
_SORA_DEFAULT_MODEL = "sora-2"
_SORA_DEFAULT_SECONDS = "5"   # closest valid value; API accepts "4", "8", "12"
# Normalise any requested duration to the nearest valid Sora value.
_SORA_VALID_SECONDS = ("4", "8", "12")


def _sora_headers() -> dict[str, str]:
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _generate_sora(prompt: str, aspect_ratio: str = "16:9") -> bytes:
    """Submit a Sora job via ``POST /v1/videos`` and block until the MP4 is ready.

    The implementation follows the Sora REST API exactly:
      1. POST /v1/videos           — submit, get Video object with ``id``
      2. GET  /v1/videos/{id}      — poll ``status`` until "completed" or "failed"
      3. GET  /v1/videos/{id}/content  — stream the MP4 bytes

    Parameters
    ----------
    prompt:
        Text description of the video to generate.
    aspect_ratio:
        One of the keys in ``_SORA_SIZE_MAP`` (e.g. ``"16:9"``, ``"9:16 (HD)"``).
        Falls back to ``"1280x720"`` when the value is unrecognised.

    Returns
    -------
    bytes
        Raw MP4 video bytes.
    """
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    if not key:
        raise ValueError(
            "OpenAI API key is not configured.  "
            "Set openai_api_key in secrets.toml."
        )

    # Map the aspect-ratio label to the Sora ``size`` parameter.
    sora_size = _SORA_SIZE_MAP.get(aspect_ratio, _SORA_SIZE_MAP["16:9"])

    # ------------------------------------------------------------------ #
    # Step 1 — submit the generation job                                  #
    # ------------------------------------------------------------------ #
    payload = {
        "model": _SORA_DEFAULT_MODEL,
        "prompt": prompt,
        "size": sora_size,
        "seconds": "8",   # valid values: "4", "8", "12"
    }
    resp = requests.post(_SORA_BASE_URL, json=payload, headers=_sora_headers(), timeout=60)

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

    video_obj = resp.json()
    video_id = video_obj.get("id")
    if not video_id:
        raise RuntimeError(
            f"Sora did not return a video ID.  Response: {resp.text[:500]}"
        )

    # ------------------------------------------------------------------ #
    # Step 2 — poll GET /v1/videos/{id} until completed or failed         #
    # ------------------------------------------------------------------ #
    poll_url = f"{_SORA_BASE_URL}/{video_id}"

    for _ in range(_MAX_POLLS):
        time.sleep(_POLL_INTERVAL_S)

        poll_resp = requests.get(poll_url, headers=_sora_headers(), timeout=30)
        poll_resp.raise_for_status()
        poll_data = poll_resp.json()

        status = poll_data.get("status", "")

        if status == "failed":
            err_obj = poll_data.get("error") or {}
            err_msg = (
                err_obj.get("message")
                if isinstance(err_obj, dict)
                else str(err_obj)
            ) or "Unknown error"
            raise RuntimeError(f"Sora generation failed: {err_msg}")

        if status == "completed":
            break
    else:
        raise TimeoutError(
            f"Sora generation did not complete within "
            f"{_MAX_POLLS * _POLL_INTERVAL_S} seconds."
        )

    # ------------------------------------------------------------------ #
    # Step 3 — download GET /v1/videos/{id}/content                       #
    # ------------------------------------------------------------------ #
    content_url = f"{_SORA_BASE_URL}/{video_id}/content"
    dl = requests.get(
        content_url,
        headers=_sora_headers(),
        params={"variant": "video"},
        timeout=180,
        stream=True,
    )
    if dl.status_code == 404:
        raise RuntimeError(
            "Sora reported completed but the content endpoint returned 404.  "
            "The video assets may have already expired."
        )
    dl.raise_for_status()

    video_bytes = dl.content
    if not video_bytes:
        raise RuntimeError(
            "Sora content endpoint returned an empty response body."
        )
    return video_bytes


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
