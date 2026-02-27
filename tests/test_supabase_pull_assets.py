import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import supabase_storage


class _FakeBucket:
    def __init__(self, files: dict[str, bytes], prefix: str):
        self._files = files
        self._prefix = prefix.strip("/")

    def list(self, prefix: str):
        normalized = prefix.strip("/")
        names = []
        for key in self._files:
            if key.startswith(f"{normalized}/"):
                names.append({"name": key.split("/")[-1]})
        return names

    def download(self, storage_path: str):
        return self._files[storage_path.strip("/")]


class _FakeStorage:
    def __init__(self, buckets: dict[str, dict[str, bytes]]):
        self._buckets = buckets

    def from_(self, bucket: str):
        return _FakeBucket(self._buckets.get(bucket, {}), "")


class _FakeClient:
    def __init__(self, buckets: dict[str, dict[str, bytes]]):
        self.storage = _FakeStorage(buckets)


def test_pull_project_assets_downloads_missing_files(monkeypatch, tmp_path: Path) -> None:
    project_id = "yasuke"
    buckets = {
        "history-forge-images": {f"{project_id}/images/s01.png": b"img"},
        "history-forge-audio": {f"{project_id}/audio/voiceover.mp3": b"aud"},
        "history-forge-videos": {f"{project_id}/videos/scene01.mp4": b"vid"},
    }
    monkeypatch.setattr(supabase_storage, "get_client", lambda: _FakeClient(buckets))

    results = supabase_storage.pull_project_assets(project_id, tmp_path)

    assert results == {"image": 1, "audio": 1, "video": 1}
    assert (tmp_path / "assets" / "images" / "s01.png").read_bytes() == b"img"
    assert (tmp_path / "assets" / "audio" / "voiceover.mp3").read_bytes() == b"aud"
    assert (tmp_path / "assets" / "videos" / "scene01.mp4").read_bytes() == b"vid"


def test_pull_project_assets_skips_existing_files(monkeypatch, tmp_path: Path) -> None:
    project_id = "yasuke"
    existing = tmp_path / "assets" / "images" / "s01.png"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"local")

    buckets = {
        "history-forge-images": {f"{project_id}/images/s01.png": b"remote"},
    }
    monkeypatch.setattr(supabase_storage, "get_client", lambda: _FakeClient(buckets))

    results = supabase_storage.pull_project_assets(project_id, tmp_path)

    assert results == {"image": 0, "audio": 0, "video": 0}
    assert existing.read_bytes() == b"local"
