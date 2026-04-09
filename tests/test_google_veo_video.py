from pathlib import Path

from src.services import google_veo_video as mod
from src.video import ai_video_clips


def test_normalize_image_input_for_google_local_file(tmp_path: Path) -> None:
    image = tmp_path / "scene.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 128)

    normalized = mod.normalize_image_input_for_google(str(image))

    assert normalized["inline_data"]
    assert normalized["image_mime_type"] == "image/png"


def test_extract_google_video_artifact_reads_nested_url() -> None:
    payload = {
        "response_type": "operation_polled",
        "response": {
            "response": {
                "candidates": [
                    {"video": {"url": "https://example.com/video.mp4"}}
                ]
            }
        },
    }

    artifact = mod.extract_google_video_artifact(payload)

    assert artifact["video_url"] == "https://example.com/video.mp4"
    assert artifact["response_type"] == "operation_polled"


def test_generate_scene_video_supports_google_provider(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "out.mp4"

    def _fake_google(**kwargs):
        out.write_bytes(b"x" * 2048)
        return {
            "ok": True,
            "provider": "google_veo_lite",
            "model": "veo-3.1-lite-generate-preview",
            "response_type": "inline",
            "video_url": "https://example.com/video.mp4",
            "output_path": str(kwargs["output_path"]),
            "error": "",
        }

    monkeypatch.setattr(ai_video_clips, "generate_google_veo_lite_video", _fake_google)

    result = ai_video_clips.generate_scene_video(
        provider="google_veo_lite",
        prompt="Prompt",
        image_path="/tmp/image.png",
        aspect_ratio="9:16",
        duration_seconds=5,
        output_path=str(out),
    )

    assert result["ok"] is True
    assert result["provider"] == "google_veo_lite"
