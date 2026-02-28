from __future__ import annotations

from pathlib import Path


import src.ai_video_generation as mod


class DummyRequest:
    def __init__(self, method: str = "GET"):
        self.method = method


class DummyResp:
    def __init__(self, status_code: int, body: dict | None = None, text: str = "", url: str = "https://api.openai.com/v1/videos", method: str = "GET", content: bytes = b""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text
        self.url = url
        self.request = DummyRequest(method)
        self.content = content

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_create_video_uses_official_endpoint_and_payload(monkeypatch):
    monkeypatch.setattr(mod, "get_secret", lambda *args, **kwargs: "sk-test")
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["payload"] = json
        return DummyResp(200, body={"id": "vid_123"}, method="POST", url=url)

    monkeypatch.setattr(mod.requests, "post", fake_post)

    job = mod.create_video("A short prompt", model="sora-2", seconds=4, size="1280x720")

    assert job["id"] == "vid_123"
    assert captured["url"] == "https://api.openai.com/v1/videos"
    assert captured["payload"]["model"] == "sora-2"
    assert captured["payload"]["seconds"] == "4"
    assert captured["payload"]["size"] == "1280x720"


def test_poll_video_completes(monkeypatch):
    states = [
        {"id": "vid_1", "status": "queued"},
        {"id": "vid_1", "status": "processing"},
        {"id": "vid_1", "status": "completed", "data": [{"url": "https://example/video.mp4"}]},
    ]

    monkeypatch.setattr(mod, "get_video", lambda _job_id: states.pop(0))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    final = mod.poll_video("vid_1", timeout_s=5)
    assert final["status"] == "completed"


def test_download_video_assets(tmp_path: Path, monkeypatch):
    job = {"id": "vid_9", "status": "completed", "data": [{"url": "https://example/video.mp4"}, {"audio_url": "https://example/audio.mp3"}]}

    def fake_get(url, headers, timeout):
        if url.endswith(".mp3"):
            return DummyResp(200, content=b"audio")
        return DummyResp(200, content=b"video")

    monkeypatch.setattr(mod.requests, "get", fake_get)

    saved = mod.download_video_assets(job, tmp_path)

    assert saved["video"] and saved["audio"]
    assert Path(saved["video"][0]).read_bytes() == b"video"
    assert Path(saved["audio"][0]).read_bytes() == b"audio"


def test_download_video_assets_from_file_ids(tmp_path: Path, monkeypatch):
    job = {
        "id": "vid_10",
        "status": "completed",
        "output": [
            {"type": "video", "file_id": "file-video-1"},
            {"type": "audio", "id": "file-audio-1"},
        ],
    }

    monkeypatch.setattr(mod, "get_secret", lambda *args, **kwargs: "sk-test")

    def fake_get(url, headers, timeout):
        assert headers["Authorization"] == "Bearer sk-test"
        if "file-audio-1" in url:
            return DummyResp(200, content=b"audio-bytes")
        if "file-video-1" in url:
            return DummyResp(200, content=b"video-bytes")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(mod.requests, "get", fake_get)

    saved = mod.download_video_assets(job, tmp_path)

    assert Path(saved["video"][0]).read_bytes() == b"video-bytes"
    assert Path(saved["audio"][0]).read_bytes() == b"audio-bytes"


def test_asset_urls_from_nested_signed_url():
    job = {
        "id": "vid_11",
        "status": "completed",
        "output": [
            {
                "asset": {
                    "type": "video",
                    "signed_url": "https://example.com/download/video.mp4?token=abc",
                }
            }
        ],
    }

    urls = mod._asset_urls_from_job(job)

    assert urls == [("video", "https://example.com/download/video.mp4?token=abc")]




def test_download_video_assets_from_file_id_with_underscore(tmp_path: Path, monkeypatch):
    job = {
        "id": "vid_12",
        "status": "completed",
        "output": {
            "video_output": {
                "asset": {
                    "id": "file_underscore_video"
                }
            }
        },
    }

    monkeypatch.setattr(mod, "get_secret", lambda *args, **kwargs: "sk-test")

    def fake_get(url, headers, timeout):
        assert "file_underscore_video" in url
        return DummyResp(200, content=b"video-underscore")

    monkeypatch.setattr(mod.requests, "get", fake_get)

    saved = mod.download_video_assets(job, tmp_path)

    assert Path(saved["video"][0]).read_bytes() == b"video-underscore"


def test_asset_urls_from_relative_path():
    job = {
        "id": "vid_13",
        "status": "completed",
        "output": {
            "video_url": "/v1/files/file-123/content"
        },
    }

    urls = mod._asset_urls_from_job(job)

    assert urls == [("video", "https://api.openai.com/v1/files/file-123/content")]


def test_sora_diagnostic_missing_models(monkeypatch):
    monkeypatch.setattr(mod, "get_secret", lambda *args, **kwargs: "sk-test")
    monkeypatch.setattr(
        mod.requests,
        "get",
        lambda url, headers, timeout: DummyResp(200, body={"data": [{"id": "gpt-4.1"}]}, url=url),
    )

    ok, message = mod.sora_diagnostic_check()

    assert not ok
    assert "not returned" in message.lower()
