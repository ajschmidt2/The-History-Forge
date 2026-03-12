from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from src.supabase_storage import get_client

log = logging.getLogger(__name__)


def _split_storage_reference(file_ref: str, bucket_name: str) -> tuple[str, str]:
    ref = str(file_ref or "").strip()
    if not ref:
        raise FileNotFoundError("Empty file reference.")

    if ref.startswith("storage://"):
        rest = ref[len("storage://") :]
        if "/" not in rest:
            raise FileNotFoundError(f"Invalid storage reference (missing object path): {file_ref}")
        parsed_bucket, object_path = rest.split("/", 1)
        parsed_bucket = parsed_bucket.strip() or bucket_name
        object_path = object_path.strip("/")
        if not object_path:
            raise FileNotFoundError(f"Invalid storage reference (empty object path): {file_ref}")
        return parsed_bucket, object_path

    return bucket_name, ref.strip("/")


def download_storage_object_to_temp(bucket_name: str, object_path: str, suffix: str | None = None) -> str:
    sb = get_client()
    if sb is None:
        raise FileNotFoundError("Supabase client is not configured; cannot fetch storage object.")

    normalized_path = str(object_path or "").strip("/")
    if not normalized_path:
        raise FileNotFoundError("Supabase object path is empty.")

    try:
        payload = sb.storage.from_(bucket_name).download(normalized_path)
    except Exception as exc:  # noqa: BLE001 - convert provider errors into user-facing path error
        raise FileNotFoundError(f"Supabase object not found: storage://{bucket_name}/{normalized_path}") from exc

    if not isinstance(payload, bytes) or not payload:
        raise FileNotFoundError(f"Downloaded empty payload from storage://{bucket_name}/{normalized_path}")

    file_suffix = suffix if suffix is not None else Path(normalized_path).suffix or None
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as temp_file:
        temp_file.write(payload)
        temp_path = temp_file.name

    log.debug("Downloaded storage object to temporary file: %s", temp_path)
    return temp_path


def resolve_upload_file(file_ref: str, bucket_name: str, suffix: str | None = None) -> str:
    local_path = Path(file_ref).expanduser()
    if os.path.exists(local_path):
        log.info("Using local file for upload: %s", local_path)
        return str(local_path)

    resolved_bucket, object_path = _split_storage_reference(file_ref, bucket_name)
    log.info("Local file not found. Attempting Supabase Storage download from bucket '%s'.", resolved_bucket)
    return download_storage_object_to_temp(resolved_bucket, object_path, suffix=suffix)
