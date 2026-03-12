from __future__ import annotations

from pathlib import Path

from src.services import storage_resolver as mod


def test_resolve_upload_file_returns_local_path(tmp_path: Path) -> None:
    local = tmp_path / "video.mp4"
    local.write_bytes(b"video")

    result = mod.resolve_upload_file(str(local), bucket_name="history-forge-videos")

    assert result == str(local)
