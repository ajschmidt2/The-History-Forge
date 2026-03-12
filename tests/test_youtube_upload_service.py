from __future__ import annotations

from pathlib import Path

import pytest

from src.services import youtube_upload as mod


def test_validate_publish_at_normalizes_zulu() -> None:
    assert mod.validate_publish_at("2026-03-14T18:30:00+00:00") == "2026-03-14T18:30:00Z"


def test_validate_publish_at_rejects_invalid_value() -> None:
    with pytest.raises(mod.YouTubeUploadError):
        mod.validate_publish_at("not-a-date")


def test_upload_video_requires_private_for_scheduling(tmp_path: Path) -> None:
    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")

    with pytest.raises(mod.YouTubeUploadError, match="privacy_status='private'"):
        mod.upload_video(
            video_path=video,
            title="Test",
            description="Desc",
            privacy_status="public",
            publish_at="2026-03-14T18:30:00Z",
        )
