"""AI video generation service — Google Veo and OpenAI Sora.

Both providers follow an async pattern:
  • Veo is proxied through a Supabase Edge Function (server-side Vertex AI call).
  • Sora is called directly from this backend module.
  • The resulting video bytes are optionally saved locally, uploaded to the
    ``generated-videos`` Supabase bucket, and recorded in the ``assets`` table.

Public API
----------
  generate_video(prompt, provider, project_id, aspect_ratio, save_dir, seconds) -> (str, str | None)
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
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

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

_OPENAI_API_BASE_URL = "https://api.openai.com"
_SORA_CREATE_URL = f"{_OPENAI_API_BASE_URL}/v1/videos"
_SORA_MODELS = {"sora-2", "sora-2-pro"}
_SORA_SECONDS = {"4", "8", "12"}


def _sora_headers() -> dict[str, str]:
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _summarize_error(resp: requests.Response) -> str:
    body = (resp.text or "").strip()
    if len(body) > 500:
        body = f"{body[:500]}..."
    return f"HTTP {resp.status_code} from {resp.request.method} {resp.url}: {body or '<empty body>'}"


def _raise_sora_http_error(resp: requests.Response, *, context: str) -> None:
    details = _summarize_error(resp)
    if resp.status_code == 400:
        raise RuntimeError(
            "OpenAI returned 400 Bad Request. The video payload is invalid for the current API; "
            "confirm model/seconds values and include a supported size (16:9=1280x720, 9:16=720x1280, 1:1=1080x1080). "
            f"{context}. {details}"
        )
    if resp.status_code == 401:
        raise PermissionError(
            "OpenAI returned 401 Unauthorized. The API key is invalid/revoked, or not being read correctly. "
            f"{context}. {details}"
        )
    if resp.status_code == 403:
        raise PermissionError(
            "OpenAI returned 403 Forbidden. This key does not have permission to use Sora in the current org/project, "
            "or policy restrictions block this request. Ensure the key belongs to the Sora-enabled project. "
            f"{context}. {details}"
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "OpenAI returned 404 Not Found. This usually means a wrong endpoint (/v1/videos is required), "
            "wrong model name (must be sora-2 or sora-2-pro), or the key belongs to a different org/project that lacks Sora access. "
            f"{context}. {details}"
        )
    resp.raise_for_status()


def create_video(
    prompt: str,
    *,
    model: str = "sora-2",
    seconds: int | str = 8,
    size: Optional[str] = None,
    input_reference: Optional[str] = None,
) -> dict[str, Any]:
    """Create a Sora job and return the raw JSON response.

    Uses the official create endpoint: POST https://api.openai.com/v1/videos.
    """
    key = get_secret("openai_api_key") or get_secret("OPENAI_API_KEY")
    if not key:
        raise ValueError("OpenAI API key is not configured. Set openai_api_key or OPENAI_API_KEY.")
    if model not in _SORA_MODELS:
        raise ValueError(f"Invalid Sora model '{model}'. Use 'sora-2' or 'sora-2-pro'.")
    seconds_value = str(seconds).strip()
    if seconds_value not in _SORA_SECONDS:
        raise ValueError(f"Invalid seconds={seconds}. Supported values are 4, 8, or 12.")
    if not prompt.strip():
        raise ValueError("Prompt cannot be empty.")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt.strip(),
        "seconds": seconds_value,
    }
    if size:
        payload["size"] = size
    if input_reference:
        payload["input_reference"] = input_reference

    resp = requests.post(_SORA_CREATE_URL, json=payload, headers=_sora_headers(), timeout=60)
    if resp.status_code >= 400:
        _raise_sora_http_error(resp, context="while creating a Sora video job")
    return resp.json()


def get_video(job_id: str) -> dict[str, Any]:
    """Return a Sora job by ID via GET /v1/videos/{id}."""
    if not job_id.strip():
        raise ValueError("job_id cannot be empty")
    url = f"{_SORA_CREATE_URL}/{job_id}"
    resp = requests.get(url, headers=_sora_headers(), timeout=60)
    if resp.status_code >= 400:
        _raise_sora_http_error(resp, context=f"while fetching Sora job '{job_id}'")
    return resp.json()


def poll_video(job_id: str, *, timeout_s: int = 600, max_backoff_s: int = 10) -> dict[str, Any]:
    """Poll a Sora job until completed/failed with exponential backoff."""
    start = time.monotonic()
    delay_s = 1
    while True:
        job = get_video(job_id)
        status = str(job.get("status", "")).lower().strip()

        if status == "completed":
            return job
        if status in {"failed", "cancelled"}:
            err = job.get("error") or job.get("message") or "Unknown error"
            raise RuntimeError(f"Sora generation {status} for job {job_id}: {err}")

        elapsed = time.monotonic() - start
        if elapsed >= timeout_s:
            raise TimeoutError(f"Timed out after {timeout_s}s waiting for Sora job {job_id}.")

        time.sleep(delay_s)
        delay_s = min(delay_s * 2, max_backoff_s)


def _asset_urls_from_job(job: dict[str, Any]) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    url_like_keys = {
        "url",
        "video",
        "video_url",
        "audio",
        "audio_url",
        "download_url",
        "asset_url",
        "signed_url",
    }

    def add_url(key: str, value: Any) -> None:
        if isinstance(value, str) and value.startswith("http"):
            kind = "audio" if "audio" in key else "video"
            urls.append((kind, value))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in url_like_keys:
                    add_url(key, value)
                walk(value)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    outputs = job.get("output") if isinstance(job.get("output"), dict) else {}
    for key in ("video", "video_url", "audio", "audio_url"):
        value = outputs.get(key)
        add_url(key, value)

    data = job.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ("url", "video_url", "audio_url"):
                value = item.get(key)
                add_url(key, value)

    walk(job)

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append((kind, url))
    return deduped


def _asset_file_ids_from_job(job: dict[str, Any]) -> list[tuple[str, str]]:
    ids: list[tuple[str, str]] = []

    def add_id(kind_hint: str, file_id: Any) -> None:
        if isinstance(file_id, str) and file_id.strip():
            kind = "audio" if "audio" in kind_hint else "video"
            ids.append((kind, file_id.strip()))

    def walk(node: Any, *, kind_hint: str = "video") -> None:
        if isinstance(node, dict):
            node_kind = str(node.get("type") or node.get("mime_type") or kind_hint).lower()
            for key in ("id", "file_id"):
                if key in node:
                    value = node[key]
                    if key == "id" and not str(value).startswith("file-"):
                        continue
                    add_id(node_kind, value)
            for key, value in node.items():
                child_hint = node_kind
                if key in {"audio", "audio_file", "audio_asset"}:
                    child_hint = "audio"
                elif key in {"video", "video_file", "video_asset"}:
                    child_hint = "video"
                walk(value, kind_hint=child_hint)
            return
        if isinstance(node, list):
            for item in node:
                walk(item, kind_hint=kind_hint)

    walk(job)

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, file_id in ids:
        if file_id not in seen:
            seen.add(file_id)
            deduped.append((kind, file_id))
    return deduped


def download_video_assets(job: dict[str, Any], dest_dir: Path | str) -> dict[str, list[str]]:
    """Download completed job assets to disk and return local paths by type."""
    job_id = str(job.get("id") or uuid.uuid4().hex)
    assets = _asset_urls_from_job(job)
    if not assets:
        file_ids = _asset_file_ids_from_job(job)
        if not file_ids:
            raise RuntimeError("Sora job completed but no downloadable asset URLs were found in the response.")
        assets = [(kind, f"{_OPENAI_API_BASE_URL}/v1/files/{file_id}/content") for kind, file_id in file_ids]

    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, list[str]] = {"video": [], "audio": []}

    for idx, (kind, url) in enumerate(assets, start=1):
        ext = ".mp4" if kind == "video" else ".mp3"
        path = out_dir / f"sora_{job_id}_{kind}_{idx}{ext}"
        dl = requests.get(url, headers=_sora_headers(), timeout=180)
        dl.raise_for_status()
        path.write_bytes(dl.content)
        saved[kind].append(str(path))

    return saved


def sora_diagnostic_check() -> tuple[bool, str]:
    """Call GET /v1/models and verify sora models are visible for this key."""
    url = f"{_OPENAI_API_BASE_URL}/v1/models"
    resp = requests.get(url, headers=_sora_headers(), timeout=60)
    if resp.status_code >= 400:
        _raise_sora_http_error(resp, context="while listing models in diagnostic mode")

    body = resp.json()
    models: set[str] = set()
    for item in body.get("data", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.add(item["id"])

    if "sora-2" in models or "sora-2-pro" in models:
        return True, "Sora models detected for this key/project."
    return (
        False,
        "Sora models were not returned by GET /v1/models for this key. "
        "Your key is likely from a different org/project than the Sora-enabled one; "
        "create a new key in the correct project and retry.",
    )


def _generate_sora(prompt: str, aspect_ratio: str = "16:9", seconds: int | str = 8) -> bytes:
    """Submit a Sora job and block until the first video asset is downloaded."""
    size = _SORA_SIZE_MAP.get(aspect_ratio, _SORA_SIZE_MAP["16:9"])
    created = create_video(prompt, model="sora-2", seconds=seconds, size=size)
    job_id = str(created.get("id") or "")
    if not job_id:
        raise RuntimeError(f"Sora did not return a job ID. Response: {json.dumps(created)[:500]}")
    final_job = poll_video(job_id, timeout_s=_MAX_POLLS * _POLL_INTERVAL_S)
    assets = download_video_assets(final_job, Path("/tmp") / "history_forge_sora_downloads")
    if not assets["video"]:
        raise RuntimeError("Sora completed but no video file was downloaded.")
    return Path(assets["video"][0]).read_bytes()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_video(
    prompt: str,
    provider: str,
    project_id: str,
    aspect_ratio: str = "16:9",
    save_dir: Optional[Path | str] = None,
    seconds: int | str = 8,
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
    seconds:
        Desired Sora clip length. Supported values are ``4``, ``8``, or ``12``.
        Ignored for providers that do not use this field.

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
        video_bytes = _generate_sora(prompt, aspect_ratio=aspect_ratio, seconds=seconds)
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
