"""Pydantic input models for MCP tool arguments.

These are used for documentation and validation. The MCP server also
accepts raw dicts (the JSON Schema in server.py is the authoritative schema).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class RunDailyShortVideoInput(BaseModel):
    topic: Optional[str] = None
    topic_direction: Optional[str] = None
    aspect_ratio: Optional[str] = None
    visual_style: Optional[str] = None
    scene_count: Optional[int] = None
    effects_style: Optional[str] = None
    voice_provider: Optional[str] = None
    openai_tts_model: Optional[str] = None
    openai_tts_voice: Optional[str] = None
    music_enabled: Optional[bool] = None
    music_relative_level: Optional[float] = None
    selected_music_track: Optional[str] = None
    subtitles_enabled: Optional[bool] = None
    target_word_count: Optional[int] = None
    target_duration_seconds: Optional[int] = None


class GenerateTopicInput(BaseModel):
    topic_direction: Optional[str] = None
    avoid_recent: Optional[bool] = True


class GetRecentDailyRunsInput(BaseModel):
    limit: Optional[int] = 10


class RerunProjectRenderInput(BaseModel):
    project_id: str
