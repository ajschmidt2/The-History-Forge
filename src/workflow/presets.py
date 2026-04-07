"""Reusable workflow presets for automation and scheduler jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from src.audio import TTS_PROVIDER_OPENAI
from src.workflow.services import PipelineOptions


@dataclass(frozen=True, slots=True)
class DailyShortPreset:
    mode: str = "topic_to_short_video"
    aspect_ratio: str = "9:16"
    output_width: int = 720
    output_height: int = 1280
    visual_style: str = "Dramatic illustration"
    effects_style: str = "Ken Burns - Standard"
    voice_provider: str = TTS_PROVIDER_OPENAI
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "ash"
    scene_count: int = 14
    subtitles_enabled: bool = False
    burn_subtitles: bool = False
    generate_srt: bool = False
    music_enabled: bool = True
    music_relative_level: float = 0.10
    target_word_count: int = 150
    target_duration_seconds: int = 60
    require_last_scene_cta: bool = True
    last_scene_cta_text: str = "Subscribe to History Crossroads for more 60-second history stories."
    ai_video_provider: str = "falai"
    image_provider: str = "falai"

    def to_pipeline_options(self, *, topic: str = "", selected_music_track: str = "") -> PipelineOptions:
        return PipelineOptions(
            number_of_scenes=self.scene_count,
            aspect_ratio=self.aspect_ratio,
            include_voiceover=True,
            include_music=self.music_enabled,
            visual_style=self.visual_style,
            include_subtitles=self.subtitles_enabled,
            enable_video_effects=True,
            video_effects_style=self.effects_style,
            selected_music_track=selected_music_track,
            music_volume_relative_to_voiceover=self.music_relative_level,
            tts_provider=self.voice_provider,
            openai_tts_model=self.openai_tts_model,
            openai_tts_voice=self.openai_tts_voice,
            automation_mode="existing_script_full_workflow",
            topic=topic,
            script_profile="youtube_short_60s",
            ai_video_provider=self.ai_video_provider,
            image_provider=self.image_provider,
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


DAILY_SHORT_PRESET = DailyShortPreset()

