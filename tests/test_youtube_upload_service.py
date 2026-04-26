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


def test_upload_video_resolves_non_local_video_and_thumbnail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    resolved_video = tmp_path / "resolved-video.mp4"
    resolved_thumb = tmp_path / "resolved-thumb.png"
    resolved_video.write_bytes(b"video")
    resolved_thumb.write_bytes(b"thumb")

    calls: list[tuple[str, str, str | None]] = []

    def fake_resolve(file_ref: str, bucket_name: str, suffix: str | None = None) -> str:
        calls.append((file_ref, bucket_name, suffix))
        if file_ref == "project-a/videos/final.mp4":
            return str(resolved_video)
        if file_ref == "project-a/thumbnails/thumb.png":
            return str(resolved_thumb)
        raise FileNotFoundError(file_ref)

    class _ThumbSetReq:
        def execute(self) -> dict[str, str]:
            return {"kind": "youtube#thumbnailSetResponse"}

    class _ThumbApi:
        def set(self, **_kwargs):
            return _ThumbSetReq()

    class _UploadReq:
        done = False

        def next_chunk(self):
            if not self.done:
                self.done = True
                return None, {"id": "abc123"}
            return None, {"id": "abc123"}

    class _VideosApi:
        def insert(self, **_kwargs):
            return _UploadReq()

    class _Youtube:
        def videos(self):
            return _VideosApi()

        def thumbnails(self):
            return _ThumbApi()

    monkeypatch.setattr(mod, "resolve_upload_file", fake_resolve)
    monkeypatch.setattr(mod, "get_youtube_service", lambda **_kwargs: _Youtube())

    result = mod.upload_video(
        video_path="project-a/videos/final.mp4",
        title="T",
        description="D",
        thumbnail_path="project-a/thumbnails/thumb.png",
    )

    assert result.video_id == "abc123"
    assert calls == [
        ("project-a/videos/final.mp4", "history-forge-videos", ".mp4"),
        ("project-a/thumbnails/thumb.png", "history-forge-images", ".png"),
    ]
    assert not resolved_video.exists()
    assert not resolved_thumb.exists()


def test_validate_youtube_credentials_recognizes_web_oauth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mod, "_web_oauth_configured", lambda: True)

    ok, msg = mod.validate_youtube_credentials(
        client_secrets_file=tmp_path / "missing-client-secrets.json",
        token_file=tmp_path / "missing-token.json",
    )

    assert ok is False
    assert "connect youtube in the app" in msg.lower()


def test_run_local_oauth_sign_in_uses_resolved_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client_file = tmp_path / "client_secrets.json"
    token_file = tmp_path / "token.json"
    calls = []

    monkeypatch.setattr(
        mod,
        "_resolve_auth_config",
        lambda **_kwargs: mod.YouTubeAuthConfig(client_secrets_file=client_file, token_file=token_file),
    )
    monkeypatch.setattr(mod, "_run_oauth_flow", lambda client, token: calls.append((client, token)))

    resolved = mod.run_local_oauth_sign_in()

    assert resolved == token_file
    assert calls == [(client_file, token_file)]
