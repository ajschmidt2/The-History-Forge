"""Effects configuration for History Forge video pipeline.

Provides two dataclasses:

* :class:`GlobalEffectsConfig` – project-wide defaults for every effect.
* :class:`SceneEffectsConfig`  – per-scene overrides; ``None`` fields inherit
  from the global config.

Use :func:`resolve_config` to merge the two into a single flat ``dict`` that
can be passed directly to :func:`~src.video.effects_pipeline.apply_effects_chain`.

Configs can be round-tripped to/from JSON and persisted in Supabase Storage
(under ``history-forge-videos/<project_id>/configs/effects_config.json``) via
:func:`save_global_config` and :func:`load_global_config`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Storage path template inside the history-forge-videos bucket.
_CONFIG_BUCKET = "history-forge-videos"
_CONFIG_STORAGE_PATH_TPL = "{project_id}/configs/effects_config.json"


# ── Global defaults ────────────────────────────────────────────────────────────

@dataclass
class GlobalEffectsConfig:
    """Project-wide default settings for the cinematic effects pipeline.

    All values are used when no per-scene override is provided.
    """

    # Ken Burns
    ken_burns_enabled: bool = True
    ken_burns_direction: str = "zoom-in-center"   # see effects_pipeline.VALID_KB_DIRECTIONS
    ken_burns_duration: float = 5.0               # seconds
    ken_burns_zoom_factor: float = 1.15

    # Map flyover (applied only when a scene is tagged as a map image)
    map_flyover_enabled: bool = True
    map_zoom_factor: float = 2.5

    # Fade
    fade_enabled: bool = True
    fade_in_duration: float = 0.4                 # seconds
    fade_out_duration: float = 0.4

    # Colour grade
    color_grade_enabled: bool = True
    color_grade_style: str = "warm"               # warm | cool | neutral | vintage

    # Film grain
    film_grain_enabled: bool = True
    film_grain_intensity: str = "medium"          # light | medium | heavy

    # Output
    output_width: int = 1920
    output_height: int = 1080
    output_fps: int = 60

    # ── Serialisation helpers ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GlobalEffectsConfig":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "GlobalEffectsConfig":
        return cls.from_dict(json.loads(text))


# ── Per-scene overrides ────────────────────────────────────────────────────────

@dataclass
class SceneEffectsConfig:
    """Per-scene overrides for the cinematic effects pipeline.

    Every field defaults to ``None``, which means "inherit from global".
    Only set a field when you want a scene-specific value.
    """

    # Ken Burns
    ken_burns_enabled: Optional[bool] = None
    ken_burns_direction: Optional[str] = None
    ken_burns_duration: Optional[float] = None
    ken_burns_zoom_factor: Optional[float] = None

    # Map flyover
    is_map_image: bool = False                    # tag: triggers flyover instead of KB
    map_flyover_enabled: Optional[bool] = None
    map_start_coords: tuple[float, float] = field(default_factory=lambda: (0.5, 0.5))
    map_end_coords: tuple[float, float] = field(default_factory=lambda: (0.5, 0.5))
    map_zoom_factor: Optional[float] = None

    # Fade
    fade_enabled: Optional[bool] = None
    fade_in_duration: Optional[float] = None
    fade_out_duration: Optional[float] = None

    # Colour grade
    color_grade_enabled: Optional[bool] = None
    color_grade_style: Optional[str] = None

    # Film grain
    film_grain_enabled: Optional[bool] = None
    film_grain_intensity: Optional[str] = None

    # ── Serialisation helpers ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert tuple fields to lists for JSON compatibility.
        for k in ("map_start_coords", "map_end_coords"):
            if isinstance(d.get(k), (list, tuple)):
                d[k] = list(d[k])
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneEffectsConfig":
        known = set(cls.__dataclass_fields__)
        filtered: dict[str, Any] = {}
        for k, v in data.items():
            if k not in known:
                continue
            if k in ("map_start_coords", "map_end_coords") and isinstance(v, list):
                filtered[k] = tuple(float(c) for c in v[:2])
            else:
                filtered[k] = v
        return cls(**filtered)


# ── Config resolver ────────────────────────────────────────────────────────────

def resolve_config(
    global_cfg: GlobalEffectsConfig,
    scene_cfg: Optional[SceneEffectsConfig] = None,
) -> dict[str, Any]:
    """Merge global defaults with scene-level overrides.

    Scene values take priority; ``None`` scene values fall back to global.

    Returns a flat ``dict`` with keyword arguments compatible with
    :func:`~src.video.effects_pipeline.apply_effects_chain`.
    """
    g = global_cfg
    s = scene_cfg or SceneEffectsConfig()

    def _pick(scene_val: Any, global_val: Any) -> Any:
        return global_val if scene_val is None else scene_val

    return dict(
        # Ken Burns
        ken_burns_enabled=_pick(s.ken_burns_enabled, g.ken_burns_enabled),
        ken_burns_direction=_pick(s.ken_burns_direction, g.ken_burns_direction),
        ken_burns_duration=_pick(s.ken_burns_duration, g.ken_burns_duration),
        ken_burns_zoom_factor=_pick(s.ken_burns_zoom_factor, g.ken_burns_zoom_factor),
        # Map flyover
        is_map_image=s.is_map_image,
        map_zoom_factor=_pick(s.map_zoom_factor, g.map_zoom_factor),
        map_start_coords=s.map_start_coords,
        map_end_coords=s.map_end_coords,
        # Fade
        fade_enabled=_pick(s.fade_enabled, g.fade_enabled),
        fade_in_duration=_pick(s.fade_in_duration, g.fade_in_duration),
        fade_out_duration=_pick(s.fade_out_duration, g.fade_out_duration),
        # Colour grade
        color_grade_enabled=_pick(s.color_grade_enabled, g.color_grade_enabled),
        color_grade_style=_pick(s.color_grade_style, g.color_grade_style),
        # Film grain
        film_grain_enabled=_pick(s.film_grain_enabled, g.film_grain_enabled),
        film_grain_intensity=_pick(s.film_grain_intensity, g.film_grain_intensity),
        # Output
        width=g.output_width,
        height=g.output_height,
        fps=g.output_fps,
    )


# ── Local persistence ──────────────────────────────────────────────────────────

def _local_config_path(project_dir: Path) -> Path:
    return project_dir / "configs" / "effects_config.json"


def save_global_config_local(config: GlobalEffectsConfig, project_dir: Path) -> bool:
    """Persist *config* as JSON in the project directory."""
    path = _local_config_path(project_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config.to_json(), encoding="utf-8")
        return True
    except OSError as exc:
        log.error("[effects_config] local save failed: %s", exc)
        return False


def load_global_config_local(project_dir: Path) -> GlobalEffectsConfig:
    """Load config from the project directory, returning defaults on failure."""
    path = _local_config_path(project_dir)
    if not path.exists():
        return GlobalEffectsConfig()
    try:
        return GlobalEffectsConfig.from_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("[effects_config] local load failed: %s; using defaults", exc)
        return GlobalEffectsConfig()


# ── Supabase persistence ───────────────────────────────────────────────────────

def save_global_config(config: GlobalEffectsConfig, project_id: str) -> bool:
    """Upload *config* JSON to Supabase Storage and local project directory.

    Uses the existing Supabase client from :mod:`src.supabase_storage`.
    Falls back gracefully when Supabase is not configured.

    Args:
        config:     The :class:`GlobalEffectsConfig` to persist.
        project_id: Slugified project identifier.

    Returns:
        ``True`` if at least one persistence path succeeded.
    """
    json_bytes = config.to_json().encode("utf-8")
    storage_path = _CONFIG_STORAGE_PATH_TPL.format(project_id=project_id)

    # Attempt Supabase upload.
    supabase_ok = False
    try:
        from src.supabase_storage import _upload_bytes, is_configured
        if is_configured():
            url = _upload_bytes(_CONFIG_BUCKET, storage_path, json_bytes, "application/json")
            supabase_ok = url is not None
    except Exception as exc:
        log.warning("[effects_config] Supabase save failed: %s", exc)

    # Also save locally (project_dir derived from project_id slug).
    from src.ui.state import PROJECTS_ROOT, slugify_project_id
    project_dir = PROJECTS_ROOT / slugify_project_id(project_id)
    local_ok = save_global_config_local(config, project_dir)

    return supabase_ok or local_ok


def load_global_config(project_id: str) -> GlobalEffectsConfig:
    """Download config from Supabase Storage (or local cache).

    Returns default :class:`GlobalEffectsConfig` when nothing is found.
    """
    storage_path = _CONFIG_STORAGE_PATH_TPL.format(project_id=project_id)

    # Try Supabase first.
    try:
        from src.supabase_storage import _download_storage_object, is_configured
        if is_configured():
            raw = _download_storage_object(_CONFIG_BUCKET, storage_path)
            if raw:
                return GlobalEffectsConfig.from_json(raw.decode("utf-8"))
    except Exception as exc:
        log.debug("[effects_config] Supabase load failed: %s", exc)

    # Fall back to local file.
    from src.ui.state import PROJECTS_ROOT, slugify_project_id
    project_dir = PROJECTS_ROOT / slugify_project_id(project_id)
    return load_global_config_local(project_dir)


# ── Scene config helpers ───────────────────────────────────────────────────────

def save_scene_configs(
    scene_configs: list[SceneEffectsConfig],
    project_id: str,
) -> bool:
    """Persist a list of per-scene configs (indexed by scene order) to JSON."""
    payload = json.dumps([sc.to_dict() for sc in scene_configs], indent=2)
    payload_bytes = payload.encode("utf-8")
    storage_path = f"{project_id}/configs/scene_effects_config.json"

    supabase_ok = False
    try:
        from src.supabase_storage import _upload_bytes, is_configured
        if is_configured():
            url = _upload_bytes(_CONFIG_BUCKET, storage_path, payload_bytes, "application/json")
            supabase_ok = url is not None
    except Exception as exc:
        log.warning("[effects_config] Supabase scene configs save failed: %s", exc)

    from src.ui.state import PROJECTS_ROOT, slugify_project_id
    project_dir = PROJECTS_ROOT / slugify_project_id(project_id)
    path = project_dir / "configs" / "scene_effects_config.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        local_ok = True
    except OSError:
        local_ok = False

    return supabase_ok or local_ok


def load_scene_configs(
    project_id: str,
    num_scenes: int,
) -> list[SceneEffectsConfig]:
    """Load per-scene configs, padding with defaults to *num_scenes* entries."""
    raw_list: list[dict] = []

    # Try Supabase.
    try:
        from src.supabase_storage import _download_storage_object, is_configured
        storage_path = f"{project_id}/configs/scene_effects_config.json"
        if is_configured():
            raw = _download_storage_object(_CONFIG_BUCKET, storage_path)
            if raw:
                raw_list = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        log.debug("[effects_config] Supabase scene configs load failed: %s", exc)

    # Fall back to local.
    if not raw_list:
        from src.ui.state import PROJECTS_ROOT, slugify_project_id
        path = PROJECTS_ROOT / slugify_project_id(project_id) / "configs" / "scene_effects_config.json"
        if path.exists():
            try:
                raw_list = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                raw_list = []

    configs: list[SceneEffectsConfig] = []
    for item in raw_list:
        try:
            configs.append(SceneEffectsConfig.from_dict(item))
        except Exception:
            configs.append(SceneEffectsConfig())

    # Pad with defaults if fewer scenes than expected.
    while len(configs) < num_scenes:
        configs.append(SceneEffectsConfig())

    return configs[:num_scenes]
