from pathlib import Path

from src.video.ai_video_clips import extract_video_url, write_video_artifact, is_valid_video_file


def test_extract_video_url_nested_dict_and_list() -> None:
    payload = {
        "result": {
            "outputs": [
                {"type": "thumbnail", "url": "https://example.com/preview.jpg"},
                {"video": {"url": "https://example.com/video.mp4?token=secret"}},
            ]
        }
    }
    assert extract_video_url(payload) == "https://example.com/video.mp4?token=secret"


def test_write_video_artifact_with_bytes(tmp_path: Path) -> None:
    output = tmp_path / "clip.mp4"
    ok, reason = write_video_artifact(b"0" * 4096, output)
    assert ok is True
    assert reason == ""
    assert is_valid_video_file(output)


def test_write_video_artifact_dict_without_video(tmp_path: Path) -> None:
    output = tmp_path / "clip.mp4"
    ok, reason = write_video_artifact({"status": "ok", "data": {}}, output)
    assert ok is False
    assert reason == "provider returned dict without video artifact"
