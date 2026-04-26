from pathlib import Path
from datetime import datetime

from src.workflow.daily_job import (
    HISTORY_CHANNEL,
    _resolve_default_music_track,
    build_daily_workflow_cron,
    load_daily_automation_settings,
    parse_daily_workflow_cron,
    run_daily_video_job,
    save_daily_automation_settings,
    update_daily_workflow_schedule,
)
from src.workflow.presets import DAILY_SHORT_PRESET


def test_daily_short_preset_pipeline_defaults():
    options = DAILY_SHORT_PRESET.to_pipeline_options(topic="Test", selected_music_track="data/music_library/test.mp3")

    assert options.aspect_ratio == "9:16"
    assert options.number_of_scenes == 14
    assert options.include_subtitles is False
    assert options.include_music is True
    assert options.music_volume_relative_to_voiceover == 0.15
    assert options.tts_provider == "openai"
    assert options.openai_tts_model == "gpt-4o-mini-tts"
    assert options.openai_tts_voice == "ash"


def test_daily_automation_settings_roundtrip(tmp_path: Path):
    settings_path = tmp_path / "daily_automation_settings.json"

    save_daily_automation_settings(
        {
            "topic_override": "Roman Roads",
            "topic_direction": "Historical mysteries",
            "selected_music_track": "data/music_library/ambient.mp3",
            "publishing": {
                "youtube_enabled": True,
                "youtube_privacy_status": "unlisted",
                "instagram_enabled": False,
            },
            "schedule": {
                "enabled": True,
                "mode": "selected_days",
                "days": ["mon", "wed", "sat"],
                "hour_local": 7,
                "timezone": "America/Indianapolis",
            },
            "preset": {
                "scene_count": 20,
                "target_word_count": 180,
                "subtitles_enabled": True,
            },
        },
        path=settings_path,
    )

    saved = load_daily_automation_settings(path=settings_path)
    assert saved["topic_override"] == "Roman Roads"
    assert saved["topic_direction"] == "Historical mysteries"
    assert saved["selected_music_track"] == "data/music_library/ambient.mp3"
    assert saved["publishing"]["youtube_enabled"] is True
    assert saved["publishing"]["youtube_privacy_status"] == "unlisted"
    assert saved["publishing"]["instagram_enabled"] is False
    assert saved["schedule"]["enabled"] is True
    assert saved["schedule"]["mode"] == "selected_days"
    assert saved["schedule"]["days"] == ["mon", "wed", "sat"]
    assert saved["preset"]["scene_count"] == 20
    assert saved["preset"]["target_word_count"] == 180
    assert saved["preset"]["subtitles_enabled"] is True
    assert saved["preset"]["openai_tts_voice"] == DAILY_SHORT_PRESET.openai_tts_voice


def test_daily_automation_settings_defaults_include_youtube_publishing(tmp_path: Path):
    saved = load_daily_automation_settings(path=tmp_path / "missing.json")

    assert saved["publishing"]["youtube_enabled"] is False
    assert saved["publishing"]["youtube_privacy_status"] == "private"
    assert saved["publishing"]["instagram_enabled"] is True


def test_run_daily_job_honors_publishing_env_overrides(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_root = Path("data/projects/generated_project")
    final_path = project_root / "renders/final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"x" * 200_000)

    def fake_run_full_workflow(*_args, **_kwargs):
        class Result:
            failed_step = ""
            warnings = []
            final_output_path = str(final_path)
        return Result()

    class UploadResult:
        video_id = "yt-env-123"

    monkeypatch.setenv("DAILY_YOUTUBE_ENABLED", "true")
    monkeypatch.setenv("DAILY_YOUTUBE_PRIVACY_STATUS", "public")
    monkeypatch.setenv("DAILY_INSTAGRAM_ENABLED", "false")
    monkeypatch.setattr("src.workflow.daily_job.generate_daily_topic", lambda **_kwargs: "Test Topic")
    monkeypatch.setattr("src.workflow.daily_job.generate_daily_short_script", lambda *_args, **_kwargs: "A" * 160)
    monkeypatch.setattr("src.workflow.daily_job.ensure_project_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.upsert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.project_dir", lambda *_args, **_kwargs: project_root)
    monkeypatch.setattr("src.workflow.daily_job.load_project_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("src.workflow.daily_job.save_project_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.save_used_topic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.run_full_workflow", fake_run_full_workflow)
    monkeypatch.setattr("src.workflow.daily_job._upload_final_to_generated_bucket", lambda *_args, **_kwargs: {
        "bucket": "generated-videos",
        "object_path": "daily-renders/test.mp4",
        "public_url": "https://example.com/final.mp4",
    })
    monkeypatch.setattr("src.workflow.daily_job._append_run_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job._ig_configured", lambda: True)
    monkeypatch.setattr("src.workflow.daily_job._tt_configured", lambda: False)
    monkeypatch.setattr("src.workflow.daily_job._sb_store.cleanup_project_intermediate_assets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("src.workflow.daily_job._validate_yt_credentials", lambda **_kwargs: (True, "ok"))

    upload_calls = []

    def fake_upload(**kwargs):
        upload_calls.append(kwargs)
        return UploadResult()

    monkeypatch.setattr("src.workflow.daily_job._yt_upload_video", fake_upload)
    monkeypatch.setattr("src.workflow.daily_job._ig_upload_reel", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Instagram should be disabled by env override")))

    save_daily_automation_settings(
        {
            "publishing": {"youtube_enabled": False, "youtube_privacy_status": "private", "instagram_enabled": True},
            "preset": {"music_enabled": False},
        },
        path=Path(HISTORY_CHANNEL.automation_settings_path),
    )

    summary = run_daily_video_job(run_date=datetime(2026, 4, 24).date(), profile=HISTORY_CHANNEL)

    assert summary["youtube_enabled"] is True
    assert summary["youtube_privacy_status"] == "public"
    assert summary["instagram_enabled"] is False
    assert summary["youtube_video_id"] == "yt-env-123"
    assert upload_calls[0]["privacy_status"] == "public"


def test_resolve_default_music_track_prefers_library_then_project_music(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_music = Path("data/projects/demo/assets/music/history-bed.mp3")
    project_music.parent.mkdir(parents=True, exist_ok=True)
    project_music.write_bytes(b"project-music")

    assert _resolve_default_music_track() == str(project_music)

    library_music = Path("data/music library/library-bed.mp3")
    library_music.parent.mkdir(parents=True, exist_ok=True)
    library_music.write_bytes(b"library-music")

    assert _resolve_default_music_track() == str(library_music)


def test_build_daily_workflow_cron_for_every_day():
    cron = build_daily_workflow_cron(
        {
            "enabled": True,
            "mode": "daily",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "hour_local": 7,
            "timezone": "America/Indianapolis",
        },
        reference_dt=datetime.fromisoformat("2026-04-24T12:00:00-04:00"),
    )

    assert cron == "0 11 * * *"


def test_build_daily_workflow_cron_for_selected_days():
    cron = build_daily_workflow_cron(
        {
            "enabled": True,
            "mode": "selected_days",
            "days": ["mon", "wed", "sat"],
            "hour_local": 7,
            "timezone": "America/Indianapolis",
        },
        reference_dt=datetime.fromisoformat("2026-04-24T12:00:00-04:00"),
    )

    assert cron == "0 11 * * MON,WED,SAT"


def test_parse_daily_workflow_cron_roundtrip():
    schedule = parse_daily_workflow_cron(
        "0 11 * * MON,WED,SAT",
        timezone_name="America/Indianapolis",
        reference_dt=datetime.fromisoformat("2026-04-24T12:00:00+00:00"),
    )

    assert schedule["enabled"] is True
    assert schedule["mode"] == "selected_days"
    assert schedule["days"] == ["mon", "wed", "sat"]
    assert schedule["hour_local"] == 7


def test_update_daily_workflow_schedule_inserts_and_removes_schedule():
    original = "name: test\n\non:\n  workflow_dispatch:\n\nenv:\n  A: B\n"
    enabled = update_daily_workflow_schedule(
        original,
        {
            "enabled": True,
            "mode": "selected_days",
            "days": ["mon", "wed"],
            "hour_local": 7,
            "timezone": "America/Indianapolis",
        },
        reference_dt=datetime.fromisoformat("2026-04-24T12:00:00-04:00"),
    )
    assert "schedule:" in enabled
    assert "cron: '0 11 * * MON,WED'" in enabled

    disabled = update_daily_workflow_schedule(
        enabled,
        {
            "enabled": False,
            "mode": "daily",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "hour_local": 7,
            "timezone": "America/Indianapolis",
        },
        reference_dt=datetime.fromisoformat("2026-04-24T12:00:00-04:00"),
    )
    assert "schedule:" not in disabled
    assert "workflow_dispatch:" in disabled


def test_run_daily_job_skips_youtube_when_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_root = Path("data/projects/generated_project")
    final_path = project_root / "renders/final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"x" * 200_000)

    def fake_run_full_workflow(*_args, **_kwargs):
        class Result:
            failed_step = ""
            warnings = []
            final_output_path = str(final_path)
        return Result()

    monkeypatch.setattr("src.workflow.daily_job.generate_daily_topic", lambda **_kwargs: "Test Topic")
    monkeypatch.setattr("src.workflow.daily_job.generate_daily_short_script", lambda *_args, **_kwargs: "A" * 160)
    monkeypatch.setattr("src.workflow.daily_job.ensure_project_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.upsert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.project_dir", lambda *_args, **_kwargs: project_root)
    monkeypatch.setattr("src.workflow.daily_job.load_project_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("src.workflow.daily_job.save_project_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.save_used_topic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.run_full_workflow", fake_run_full_workflow)
    monkeypatch.setattr("src.workflow.daily_job._upload_final_to_generated_bucket", lambda *_args, **_kwargs: {
        "bucket": "generated-videos",
        "object_path": "daily-renders/test.mp4",
        "public_url": "https://example.com/final.mp4",
    })
    monkeypatch.setattr("src.workflow.daily_job._append_run_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job._ig_configured", lambda: False)
    monkeypatch.setattr("src.workflow.daily_job._tt_configured", lambda: False)
    monkeypatch.setattr("src.workflow.daily_job._sb_store.cleanup_project_intermediate_assets", lambda *_args, **_kwargs: {})

    upload_calls = []
    monkeypatch.setattr("src.workflow.daily_job._validate_yt_credentials", lambda **_kwargs: (True, "ok"))
    monkeypatch.setattr("src.workflow.daily_job._yt_upload_video", lambda **_kwargs: upload_calls.append(_kwargs))

    save_daily_automation_settings(
        {
            "publishing": {"youtube_enabled": False, "youtube_privacy_status": "private"},
            "preset": {"music_enabled": False},
        },
        path=Path(HISTORY_CHANNEL.automation_settings_path),
    )

    summary = run_daily_video_job(run_date=datetime(2026, 4, 24).date(), profile=HISTORY_CHANNEL)

    assert summary["youtube_enabled"] is False
    assert summary["youtube_video_id"] == ""
    assert upload_calls == []


def test_run_daily_job_skips_instagram_when_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_root = Path("data/projects/generated_project")
    final_path = project_root / "renders/final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"x" * 200_000)

    def fake_run_full_workflow(*_args, **_kwargs):
        class Result:
            failed_step = ""
            warnings = []
            final_output_path = str(final_path)
        return Result()

    monkeypatch.setattr("src.workflow.daily_job.generate_daily_topic", lambda **_kwargs: "Test Topic")
    monkeypatch.setattr("src.workflow.daily_job.generate_daily_short_script", lambda *_args, **_kwargs: "A" * 160)
    monkeypatch.setattr("src.workflow.daily_job.ensure_project_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.upsert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.project_dir", lambda *_args, **_kwargs: project_root)
    monkeypatch.setattr("src.workflow.daily_job.load_project_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("src.workflow.daily_job.save_project_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.save_used_topic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.run_full_workflow", fake_run_full_workflow)
    monkeypatch.setattr("src.workflow.daily_job._upload_final_to_generated_bucket", lambda *_args, **_kwargs: {
        "bucket": "generated-videos",
        "object_path": "daily-renders/test.mp4",
        "public_url": "https://example.com/final.mp4",
    })
    monkeypatch.setattr("src.workflow.daily_job._append_run_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job._validate_yt_credentials", lambda **_kwargs: (False, "skip"))
    monkeypatch.setattr("src.workflow.daily_job._tt_configured", lambda: False)
    monkeypatch.setattr("src.workflow.daily_job._sb_store.cleanup_project_intermediate_assets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("src.workflow.daily_job._ig_configured", lambda: True)

    instagram_calls = []
    monkeypatch.setattr("src.workflow.daily_job._ig_upload_reel", lambda **_kwargs: instagram_calls.append(_kwargs))

    save_daily_automation_settings(
        {
            "publishing": {
                "youtube_enabled": False,
                "youtube_privacy_status": "private",
                "instagram_enabled": False,
            },
            "preset": {"music_enabled": False},
        },
        path=Path(HISTORY_CHANNEL.automation_settings_path),
    )

    summary = run_daily_video_job(run_date=datetime(2026, 4, 24).date(), profile=HISTORY_CHANNEL)

    assert summary["instagram_enabled"] is False
    assert summary["instagram_media_id"] == ""
    assert instagram_calls == []


def test_run_daily_job_uploads_to_youtube_when_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_root = Path("data/projects/generated_project")
    final_path = project_root / "renders/final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"x" * 200_000)

    def fake_run_full_workflow(*_args, **_kwargs):
        class Result:
            failed_step = ""
            warnings = []
            final_output_path = str(final_path)
        return Result()

    class UploadResult:
        video_id = "yt123"

    monkeypatch.setattr("src.workflow.daily_job.generate_daily_topic", lambda **_kwargs: "Test Topic")
    monkeypatch.setattr("src.workflow.daily_job.generate_daily_short_script", lambda *_args, **_kwargs: "A" * 160)
    monkeypatch.setattr("src.workflow.daily_job.ensure_project_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.upsert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.project_dir", lambda *_args, **_kwargs: project_root)
    monkeypatch.setattr("src.workflow.daily_job.load_project_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("src.workflow.daily_job.save_project_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.save_used_topic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.run_full_workflow", fake_run_full_workflow)
    monkeypatch.setattr("src.workflow.daily_job._upload_final_to_generated_bucket", lambda *_args, **_kwargs: {
        "bucket": "generated-videos",
        "object_path": "daily-renders/test.mp4",
        "public_url": "https://example.com/final.mp4",
    })
    monkeypatch.setattr("src.workflow.daily_job._append_run_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job._ig_configured", lambda: False)
    monkeypatch.setattr("src.workflow.daily_job._tt_configured", lambda: False)
    monkeypatch.setattr("src.workflow.daily_job._sb_store.cleanup_project_intermediate_assets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("src.workflow.daily_job._validate_yt_credentials", lambda **_kwargs: (True, "ok"))

    upload_calls = []

    def fake_upload(**kwargs):
        upload_calls.append(kwargs)
        return UploadResult()

    monkeypatch.setattr("src.workflow.daily_job._yt_upload_video", fake_upload)

    save_daily_automation_settings(
        {
            "publishing": {"youtube_enabled": True, "youtube_privacy_status": "unlisted"},
            "preset": {"music_enabled": False},
        },
        path=Path(HISTORY_CHANNEL.automation_settings_path),
    )

    summary = run_daily_video_job(run_date=datetime(2026, 4, 24).date(), profile=HISTORY_CHANNEL)

    assert summary["youtube_enabled"] is True
    assert summary["youtube_privacy_status"] == "unlisted"
    assert summary["youtube_video_id"] == "yt123"
    assert upload_calls[0]["privacy_status"] == "unlisted"


def test_run_daily_job_disables_music_when_track_is_unavailable_on_runner(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project_root = Path("data/projects/generated_project")
    final_path = project_root / "renders/final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"x" * 200_000)

    workflow_calls = []

    def fake_run_full_workflow(*_args, **_kwargs):
        workflow_calls.append(_kwargs["pipeline"] if "pipeline" in _kwargs else _args[1].pipeline)

        class Result:
            failed_step = ""
            warnings = []
            final_output_path = str(final_path)

        return Result()

    monkeypatch.setattr("src.workflow.daily_job.generate_daily_topic", lambda **_kwargs: "Test Topic")
    monkeypatch.setattr("src.workflow.daily_job.generate_daily_short_script", lambda *_args, **_kwargs: "A" * 160)
    monkeypatch.setattr("src.workflow.daily_job.ensure_project_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.upsert_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.project_dir", lambda *_args, **_kwargs: project_root)
    monkeypatch.setattr("src.workflow.daily_job.load_project_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("src.workflow.daily_job.save_project_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.save_used_topic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job.run_full_workflow", fake_run_full_workflow)
    monkeypatch.setattr("src.workflow.daily_job._upload_final_to_generated_bucket", lambda *_args, **_kwargs: {
        "bucket": "generated-videos",
        "object_path": "daily-renders/test.mp4",
        "public_url": "https://example.com/final.mp4",
    })
    monkeypatch.setattr("src.workflow.daily_job._append_run_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.workflow.daily_job._validate_yt_credentials", lambda **_kwargs: (False, "skip"))
    monkeypatch.setattr("src.workflow.daily_job._ig_configured", lambda: False)
    monkeypatch.setattr("src.workflow.daily_job._tt_configured", lambda: False)
    monkeypatch.setattr("src.workflow.daily_job._sb_store.cleanup_project_intermediate_assets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "src.workflow.daily_job.resolve_music_track_for_project",
        lambda *_args, **_kwargs: {"selected_track": "missing.mp3", "resolved_path": "", "copied_to_project": False, "file_exists": False},
    )

    save_daily_automation_settings(
        {
            "selected_music_track": "C:/does/not/exist/music.mp3",
            "publishing": {"youtube_enabled": False, "youtube_privacy_status": "private", "instagram_enabled": False},
            "preset": {"music_enabled": True},
        },
        path=Path(HISTORY_CHANNEL.automation_settings_path),
    )

    summary = run_daily_video_job(run_date=datetime(2026, 4, 24).date(), profile=HISTORY_CHANNEL)

    assert summary["music_track"] == ""
    assert summary["warnings"]
    assert "Continuing without music" in summary["warnings"][0]
    assert workflow_calls
    assert workflow_calls[0].include_music is False
