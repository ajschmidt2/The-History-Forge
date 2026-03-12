from __future__ import annotations

from pathlib import Path

from src.services.youtube_upload import YouTubeUploadError, upload_video

VIDEO_FILE = Path("data/projects/.../renders/final.mp4")


def main() -> None:
    try:
        result = upload_video(
            video_path=VIDEO_FILE,
            title=VIDEO_FILE.stem,
            description="Uploaded via History Forge YouTube service",
            privacy_status="private",
        )
        print(f"Upload complete. Video ID: {result.video_id}")
    except YouTubeUploadError as error:
        print(f"YouTube upload error: {error}")
    except Exception as error:  # noqa: BLE001
        print(f"Upload failed: {error}")


if __name__ == "__main__":
    main()
