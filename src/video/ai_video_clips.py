"""
src/video/ai_video_clips.py

Animates the first and middle generated scene images into true AI video clips.
Supports three providers:
  - google_veo_lite: Gemini/Veo Fast image-to-video
  - falai: fal.ai Wan image-to-video fallback

Provider is selected at call time via the `provider` argument.
"""

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import requests

from src.config import get_secret
from src.services.fal_video_test import (
    DEFAULT_FAL_VIDEO_MODEL,
    WORKING_TEST_MODEL_SLUG,
    extract_video_url as _extract_video_url,
    generate_fal_video_from_image,
    validate_fal_model_slug,
)
from src.services.google_veo_video import (
    DEFAULT_GOOGLE_VIDEO_MODEL,
    generate_google_veo_lite_video,
)
from src.video.utils import resolve_ffmpeg_exe

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ("google_veo_lite", "falai", "auto")
SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}
MIN_VIDEO_BYTES = 1024
_VIDEO_SAFETY_SANITIZATIONS: list[tuple[str, str]] = [
    (r"\bchildren\b", "family members"),
    (r"\bchild\b", "family member"),
    (r"\bkids?\b", "family members"),
    (r"\bbab(?:y|ies)\b", "small figures"),
    (r"\binfant(?:s)?\b", "small figures"),
    (r"\bflames?\b", "firelight"),
    (r"\bburning\b", "glowing"),
    (r"\bfire\b", "lamplight"),
    (r"\bdead(?:ly)?\b", "lost"),
    (r"\bdeath(?:s)?\b", "loss"),
    (r"\bkill(?:ing|ings|ed|s)?\b", "tragedy"),
    (r"\bmurder(?:ed|er|ers|ing|s)?\b", "tragedy"),
    (r"\bviolen(?:ce|t)\b", "conflict"),
    (r"\bbrutal(?:ity|ly)?\b", "harsh"),
    (r"\bterror\b", "fear"),
    (r"\bpanic\b", "alarm"),
]


def extract_video_url(obj: Any) -> str | None:
    """Recursively extract a likely video URL from a provider response."""
    return _extract_video_url(obj)


def is_valid_video_file(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.exists() and candidate.is_file() and candidate.stat().st_size >= MIN_VIDEO_BYTES


def write_video_artifact(artifact: Any, output_path: str | Path) -> tuple[bool, str]:
    """Persist a provider video artifact to disk.

    Supports raw bytes and structured responses containing a video URL.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(artifact, (bytes, bytearray)):
        output.write_bytes(bytes(artifact))
        return (True, "") if is_valid_video_file(output) else (False, "video artifact was empty or too small")

    if isinstance(artifact, dict):
        video_url = extract_video_url(artifact)
        if not video_url:
            return False, "provider returned dict without video artifact"
        try:
            with requests.get(video_url, timeout=180, stream=True) as response:
                response.raise_for_status()
                with output.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            handle.write(chunk)
        except Exception as exc:  # noqa: BLE001
            return False, f"video artifact download failed: {exc}"
        return (True, "") if is_valid_video_file(output) else (False, "downloaded video artifact was empty or too small")

    return False, f"unsupported video artifact type: {type(artifact).__name__}"


def _clip_target_indexes(scene_count: int) -> tuple[int, int, int, int]:
    safe_scene_count = max(int(scene_count), 1)
    if safe_scene_count == 1:
        return (0, 0, 0, 0)
    return (
        0,
        safe_scene_count // 4,
        safe_scene_count // 2,
        (safe_scene_count * 3) // 4,
    )


def normalize_ai_video_provider(provider: object, fallback: str = "google_veo_lite") -> str:
    """Normalize provider settings and migrate removed legacy choices."""
    value = (str(provider or fallback).strip().lower() or fallback)
    if value == "sora":
        return fallback
    return value if value in SUPPORTED_PROVIDERS else fallback


# ---------------------------------------------------------------------------
# Scene asset helpers
# ---------------------------------------------------------------------------

def _find_scene_images(project_id: str) -> list[Path]:
    """Return sorted generated scene images for this project."""
    base = Path("data/projects") / project_id / "assets" / "images"
    if not base.exists():
        return []
    images = sorted(base.glob("s*.png"))
    if not images:
        images = sorted(base.glob("*.png"))
    return images


def _extract_prompt_str(raw: object) -> str:
    """Extract a plain prompt string from a raw scenes.json value.

    The image_prompt field may be:
    - A bare string: returned as-is
    - A dict: ``raw["prompt"]`` returned
    - A stringified dict with trailing metadata, e.g.
      ``"{'scene': 1, 'prompt': 'Wide shot of...'}\n\nVisual intent: ..."``
      â†’ the 'prompt' value is extracted via regex
    """
    if not raw:
        return ""
    if isinstance(raw, dict):
        return str(raw.get("prompt") or "")
    s = str(raw).strip()
    if s.startswith("{"):
        # Try regex to extract 'prompt': '...' or "prompt": "..." value
        # Handles trailing metadata after the closing brace
        m = re.search(r"""['"]prompt['"]\s*:\s*['"](.+?)['"]\s*\}""", s, re.DOTALL)
        if m:
            return m.group(1).strip()
    return s


def _find_scene_prompts(project_id: str) -> list[dict[str, str]]:
    """Return image/video prompts in scene order from scenes.json."""
    scenes_path = Path("data/projects") / project_id / "scenes.json"
    if not scenes_path.exists():
        return []
    try:
        scenes = json.loads(scenes_path.read_text())
        rows = []
        for s in scenes:
            row = {
                "title": _extract_prompt_str(s.get("title") or ""),
                "script_excerpt": _extract_prompt_str(s.get("script_excerpt") or ""),
                "scene_summary": _extract_prompt_str(s.get("scene_summary") or ""),
                "image_prompt": _extract_prompt_str(s.get("image_prompt") or s.get("prompt") or ""),
                "video_prompt": _extract_prompt_str(s.get("video_prompt") or ""),
                "negative_prompt": _extract_prompt_str(s.get("negative_prompt") or ""),
            }
            visual_context = s.get("visual_context") if isinstance(s.get("visual_context"), dict) else {}
            if visual_context:
                row["visual_context"] = visual_context
            rows.append(row)
        return rows
    except Exception:
        return []


def _build_motion_prompt(image_prompt: str) -> str:
    base = image_prompt.strip().rstrip(".")
    return (
        f"{base}. Animate with natural cinematic motion â€” elements move realistically, "
        "atmosphere shifts, light and shadow animate across the scene. "
        "Dramatic documentary style, historically immersive, slow deliberate movement."
    )


def _sanitize_video_prompt(text: object) -> str:
    result = re.sub(r"\s+", " ", str(text or "")).strip()
    for pattern, replacement in _VIDEO_SAFETY_SANITIZATIONS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"\bno text overlays?\b", "no text", result, flags=re.IGNORECASE)
    result = re.sub(r"\bno captions?\b", "no text", result, flags=re.IGNORECASE)
    result = re.sub(r"\bno logos?\b", "no modern branding", result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip(" ,.;")


def _trim_sentence(text: object, limit: int) -> str:
    cleaned = _sanitize_video_prompt(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0].rstrip(" ,.;") + "..."


def _build_prompt_variants(
    *,
    base_prompt: str,
    label: str,
    packed: dict[str, object] | None,
    aspect_ratio: str,
) -> list[str]:
    packed = packed if isinstance(packed, dict) else {}
    prompt_spec = packed.get("prompt_spec", {}) if isinstance(packed.get("prompt_spec"), dict) else {}
    video_spec = packed.get("video_spec", {}) if isinstance(packed.get("video_spec"), dict) else {}
    visual_context = packed.get("visual_context", {}) if isinstance(packed.get("visual_context"), dict) else {}

    subject = _trim_sentence(
        prompt_spec.get("primary_subject")
        or visual_context.get("character_name")
        or packed.get("title")
        or "historical subject",
        80,
    )
    setting = _trim_sentence(
        prompt_spec.get("setting/location")
        or visual_context.get("location")
        or packed.get("scene_summary")
        or "historical setting",
        90,
    )
    period = _trim_sentence(
        prompt_spec.get("time_period")
        or visual_context.get("time_period")
        or "historical period",
        60,
    )
    atmosphere = _trim_sentence(
        visual_context.get("visual_atmosphere")
        or prompt_spec.get("emotional_tone")
        or "tense documentary atmosphere",
        70,
    )
    opening = _trim_sentence(video_spec.get("opening frame description") or packed.get("scene_summary") or "", 120)
    motion = _trim_sentence(video_spec.get("subject motion") or prompt_spec.get("visible_action") or "subtle natural movement", 120)
    camera = {
        "opening": "slow push in",
        "q2": "gentle lateral move",
        "q3": "controlled low-angle drift",
        "q4": "slow reveal pull back",
    }.get(label, "slow cinematic drift")

    concise = (
        f"Cinematic historical documentary shot. Subject: {subject}. Setting: {setting}, {period}. "
        f"Action: {motion}. Camera: {camera}. Atmosphere: {atmosphere}. "
        f"Keep identity stable, motion subtle, and the result safe, non-graphic, and realistic. "
        f"Treat prompt words as instructions only, never as visible writing. "
        f"No text, no letters, no logos, no title cards, no signage, no captions, no subtitles, no explicit injury, no visible harm. Compose for {aspect_ratio}."
    )
    minimal = (
        f"Animate this historical image into a safe cinematic documentary clip. Subject: {subject}. "
        f"Setting: {setting}. Camera: {camera}. Subtle realistic motion only. "
        f"Treat prompt words as instructions only, never as visible writing. "
        f"No text, no letters, no captions, no title cards, no signage, no explicit harm, no gore, no chaos. Aspect ratio {aspect_ratio}."
    )
    variants = [base_prompt, concise, minimal]
    if opening:
        variants.insert(
            1,
            f"{opening}. Camera: {camera}. Keep the scene historically grounded, safe, non-graphic, and visually distinct from neighboring stills. "
            f"Treat prompt words as instructions only, never as visible writing. No text, no letters, no captions, no signage. Aspect ratio {aspect_ratio}.",
        )
    deduped: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        cleaned = _sanitize_video_prompt(variant)
        if len(cleaned) > 700:
            cleaned = _trim_sentence(cleaned, 700)
        phrase = "Treat prompt words as instructions only, never as visible writing."
        if "never as visible writing" not in cleaned.lower():
            available = 700 - len(phrase) - 1
            if available > 0 and len(cleaned) > available:
                cleaned = cleaned[:available].rsplit(" ", 1)[0].rstrip(" ,.;")
            cleaned = f"{cleaned} {phrase}".strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def _write_automation_debug(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _validate_fal_inputs(prompt: str, image_path: Optional[Path], aspect_ratio: str, duration_seconds: int) -> None:
    if not str(prompt or "").strip():
        raise ValueError("Prompt cannot be empty.")
    if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect ratio '{aspect_ratio}'.")
    if duration_seconds < 1 or duration_seconds > 12:
        raise ValueError("Duration must be between 1 and 12 seconds.")
    if image_path is not None and not image_path.exists():
        raise ValueError(f"Image path does not exist: {image_path}")

def _resolve_fal_model(workflow_logger: logging.Logger) -> tuple[str, bool]:
    override_model = str(get_secret("fal_video_model", "") or "").strip()
    candidate = override_model or DEFAULT_FAL_VIDEO_MODEL
    ok, detail = validate_fal_model_slug(candidate)
    if not ok:
        workflow_logger.warning(
            "FAL_AUTOMATION_USING_SHARED_HELPER invalid_model_override=%s fallback_model=%s reason=%s",
            override_model or "<none>",
            DEFAULT_FAL_VIDEO_MODEL,
            detail,
        )
        return DEFAULT_FAL_VIDEO_MODEL, bool(override_model)
    return detail, bool(override_model)


def _generate_falai_video_clip(
    *,
    prompt: str,
    output_path: Path,
    clip_label: str,
    project_id: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    image_path: Optional[Path] = None,
    workflow_logger: logging.Logger | None = None,
) -> tuple[bool, str]:
    _validate_fal_inputs(prompt, image_path, aspect_ratio, duration_seconds)
    wlog = workflow_logger or logger
    debug_path = Path("data/projects") / project_id / "debug" / f"fal_video_{clip_label}.json"
    model_slug, override_applied = _resolve_fal_model(wlog)
    if not override_applied:
        assert model_slug == WORKING_TEST_MODEL_SLUG

    if image_path is None:
        reason = "image input is required (upload, URL, data URI, or local file path)"
        _write_automation_debug(debug_path, {"ok": False, "clip": clip_label, "error": reason})
        return False, reason

    prompt_clean = str(prompt or "").strip()
    if not prompt_clean:
        reason = "prompt cannot be empty"
        _write_automation_debug(debug_path, {"ok": False, "clip": clip_label, "error": reason, "image_path": str(image_path)})
        return False, reason

    wlog.info("FAL_AUTOMATION_SHARED_HELPER_ACTIVE")
    try:
        result = generate_fal_video_from_image(
            model=model_slug,
            prompt=prompt_clean,
            image_source=str(image_path),
            output_path=output_path,
            duration=duration_seconds,
            aspect_ratio=aspect_ratio,
            fail_loud_missing_video_artifact=True,
        )
    except RuntimeError as exc:
        _write_automation_debug(
            debug_path,
            {
                "ok": False,
                "model": model_slug,
                "clip": clip_label,
                "image_path": str(image_path),
                "output_path": str(output_path),
                "error": str(exc),
            },
        )
        raise

    response_type = str(result.get("response_type") or "none")
    response_keys = result.get("response_keys") if isinstance(result.get("response_keys"), list) else []
    wlog.info(
        "FAL_AUTOMATION_USING_SHARED_HELPER model=%s clip=%s response_type=%s response_keys=%s",
        model_slug,
        clip_label,
        response_type,
        response_keys[:20],
    )

    metadata = {
        "ok": bool(result.get("ok")),
        "model": model_slug,
        "clip": clip_label,
        "image_path": str(image_path),
        "output_path": str(output_path),
        "response_type": response_type,
        "response_keys": response_keys,
        "video_url": result.get("video_url", ""),
        "error": result.get("error", ""),
    }
    _write_automation_debug(debug_path, metadata)

    if bool(result.get("ok")) and output_path.exists() and output_path.stat().st_size >= MIN_VIDEO_BYTES:
        wlog.info("FAL_AUTOMATION_USING_SHARED_HELPER success clip=%s output_path=%s", clip_label, output_path)
        return True, ""

    reason = str(result.get("error") or "provider returned empty response")
    wlog.warning("FAL_AUTOMATION_USING_SHARED_HELPER failed clip=%s error=%s", clip_label, reason[:240])
    return False, reason


def generate_scene_video(
    provider,
    prompt,
    image_path,
    aspect_ratio,
    duration_seconds,
    output_path,
    debug_dir=None,
):
    """Provider abstraction for scene clip generation."""
    provider_name = normalize_ai_video_provider(provider)
    if provider_name == "auto":
        provider_name = normalize_ai_video_provider(get_secret("HF_VIDEO_PROVIDER", "google_veo_lite"))
    output = Path(output_path)

    if provider_name == "falai":
        model_slug, _ = _resolve_fal_model(logger)
        if not image_path:
            reason = "image input is required (upload, URL, data URI, or local file path)"
            return {
                "ok": False,
                "provider": "falai",
                "model": model_slug,
                "response_type": "fal_subscribe",
                "video_url": "",
                "output_path": str(output),
                "error": reason,
            }
        fal_result = generate_fal_video_from_image(
            model=model_slug,
            prompt=str(prompt or "").strip(),
            image_source=str(image_path),
            output_path=output,
            duration=duration_seconds,
            aspect_ratio=aspect_ratio,
            fail_loud_missing_video_artifact=True,
        )
        ok = bool(fal_result.get("ok")) and output.exists() and output.stat().st_size >= MIN_VIDEO_BYTES
        reason = str(fal_result.get("error") or "")
        return {
            "ok": bool(ok),
            "provider": "falai",
            "model": model_slug,
            "response_type": str(fal_result.get("response_type") or "fal_subscribe"),
            "video_url": str(fal_result.get("video_url") or ""),
            "output_path": str(output),
            "error": "" if ok else reason,
        }

    if provider_name == "google_veo_lite":
        return generate_google_veo_lite_video(
            prompt=str(prompt or ""),
            image_source=str(image_path) if image_path else "",
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            output_path=str(output),
            debug_dir=debug_dir,
            model=str(get_secret("HF_GOOGLE_VIDEO_MODEL", DEFAULT_GOOGLE_VIDEO_MODEL) or DEFAULT_GOOGLE_VIDEO_MODEL),
        )

    return {
        "ok": False,
        "provider": provider_name,
        "model": "",
        "response_type": "unsupported_provider",
        "video_url": "",
        "output_path": str(output),
        "error": f"Provider '{provider_name}' is not supported by generate_scene_video.",
    }



# ---------------------------------------------------------------------------
# Orientation normalization
# ---------------------------------------------------------------------------

def _normalize_clip_orientation(src: Path, width: int, height: int) -> None:
    """Re-encode clip in-place to exact dimensions, stripping rotation metadata.

    Some generators occasionally embed a rotation tag that
    causes the clip to appear sideways when composited. ffmpeg auto-rotates on
    read by default, so applying scale+crop after decoding always produces the
    correct pixel layout. We strip all container metadata on write so the tag
    cannot affect downstream players or the ffmpeg concat step.
    """
    tmp = src.with_suffix(".norm.mp4")
    cmd = [
        resolve_ffmpeg_exe(), "-y",
        "-i", str(src),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            "setsar=1"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an",
        "-map_metadata", "-1",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        tmp.replace(src)
    except subprocess.CalledProcessError as exc:
        logger.warning(
            f"ai_video_clips: orientation normalization failed for {src.name} â€” "
            f"{exc.stderr.decode(errors='replace')[-300:]}"
        )
        if tmp.exists():
            tmp.unlink()


def _clip_manifest_path(project_id: str) -> Path:
    return Path("data/projects") / project_id / "clip_manifest.json"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_ai_video_clips(
    project_id: str,
    tmp_dir: Path,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    provider: str = "google_veo_lite",
    workflow_logger=None,
    clip_done_callback=None,
) -> tuple:
    """
    Main entry point called by the automation step runner.

    Args:
        project_id:       Active History Forge project ID.
        tmp_dir:          Directory to write output MP4 files.
        aspect_ratio:     "9:16", "16:9", or "1:1".
        duration_seconds: Clip length for image-to-video providers.
        provider:         "google_veo_lite", "falai", or "auto".

    Returns:
        (opening_clip_path | None, mid_clip_path | None)

    Raises:
        RuntimeError: If provider credentials are missing or Edge Function is
                      not deployed. Surfaces to the automation UI.
        ValueError:   If an unknown provider is specified.
    """
    provider = normalize_ai_video_provider(provider)
    if provider == "auto":
        provider = normalize_ai_video_provider(get_secret("HF_VIDEO_PROVIDER", "google_veo_lite"))
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"ai_video_clips: unknown provider '{provider}'. "
            f"Use one of: {SUPPORTED_PROVIDERS}"
        )

    _wlog = workflow_logger or logger

    images = _find_scene_images(project_id)
    prompts = _find_scene_prompts(project_id)

    if not prompts and provider in ("falai", "google_veo_lite"):
        _wlog.warning(
            "ai_video_clips [%s]: no scene prompts found for project %s, skipping",
            provider, project_id,
        )
        return None, None, None, None

    tmp_dir.mkdir(parents=True, exist_ok=True)
    _wlog.info("ai_video_clips project=%s provider=%s images=%d prompts=%d", project_id, provider, len(images), len(prompts))
    size_str = {"16:9": "1280x720", "9:16": "720x1280", "1:1": "1080x1080"}.get(aspect_ratio, "720x1280")
    _clip_w, _clip_h = (int(v) for v in size_str.split("x"))

    # Camera motion instruction per clip position â€” reinforces narrative pacing
    # and gives each clip a distinct cinematic feel.
    _CLIP_CAMERA_MOTIONS = {
        "opening": "slow dramatic push-in establishing shot",
        "q2":      "medium shot, subtle rack focus",
        "q3":      "low angle dramatic upward tilt",
        "q4":      "slow pull-out revealing shot",
    }

    def _compact_prompt_text(value: object, limit: int = 260) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;") + "..."

    def _condense_style_guidance(value: object, limit: int = 8) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        items: list[str] = []
        seen: set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("-"):
                continue
            bullet = stripped.lstrip("-").strip().strip(".")
            if not bullet:
                continue
            lowered = bullet.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            items.append(bullet)
            if len(items) >= limit:
                break
        return ", ".join(items[:limit])

    def _trim_video_prompt(value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        for marker in ("Global visual style control:", "Visual style guidance:"):
            if marker in text:
                text = text.split(marker, 1)[0].strip()
        return text

    def _neighbor_brief(idx: int) -> str:
        if idx < 0 or idx >= len(prompts):
            return ""
        packed = prompts[idx] if isinstance(prompts[idx], dict) else {}
        for key in ("scene_summary", "title", "script_excerpt", "image_prompt"):
            value = _compact_prompt_text(packed.get(key, ""), limit=220)
            if value:
                return value
        return ""

    def _get_prompt(idx: int, label: str = "") -> str:
        packed = prompts[idx] if idx < len(prompts) else {}
        raw_video = str(packed.get("video_prompt", "") or "").strip() if isinstance(packed, dict) else ""
        raw_image = str(packed.get("image_prompt", "") or "").strip() if isinstance(packed, dict) else str(packed or "").strip()
        negative = str(packed.get("negative_prompt", "") or "").strip() if isinstance(packed, dict) else ""
        packed_spec = packed.get("prompt_spec", {}) if isinstance(packed, dict) else {}
        packed_video_spec = packed.get("video_spec", {}) if isinstance(packed, dict) else {}
        # Build a rich visual context prefix from the enriched visual_context dict
        vc = packed.get("visual_context", {}) if isinstance(packed, dict) else {}
        vc_parts = []
        for _k, _label in [
            ("time_period", ""), ("location", ""), ("clothing_style", ""),
            ("visual_atmosphere", ""), ("character_name", "Subject"),
            ("character_appearance", "Appearance"), ("visual_style", "Style"),
            ("color_palette", "Palette"),
        ]:
            _val = str(vc.get(_k, "") or "").strip()
            if _val:
                vc_parts.append(f"{_label}: {_val}" if _label else _val)
        vc_prefix = ", ".join(vc_parts) + ". " if vc_parts else ""
        # Per-clip camera motion appended to base prompt
        camera_motion = _CLIP_CAMERA_MOTIONS.get(label, "slow dolly-in with subtle lateral drift")
        opening = _compact_prompt_text(packed_video_spec.get("opening frame description", ""), limit=220)
        subject_motion = _compact_prompt_text(packed_video_spec.get("subject motion", ""), limit=220)
        ending = _compact_prompt_text(packed_video_spec.get("ending frame description", ""), limit=180)
        uniqueness = _compact_prompt_text(packed_spec.get("scene_uniqueness_note", ""), limit=140)
        keywords = ", ".join((packed_spec.get("anchor_keywords", []) or [])[:6]) if isinstance(packed_spec, dict) else ""
        style_guidance = _condense_style_guidance(packed_spec.get("global_visual_style_control", ""), limit=8)

        compact_base_parts = [
            opening,
            subject_motion,
            f"Camera motion: {camera_motion}",
            "Keep motion cinematic, subtle, and historically grounded.",
            ending,
        ]
        if keywords:
            compact_base_parts.append(f"Anchor keywords: {keywords}")
        if uniqueness:
            compact_base_parts.append(f"Distinct beat: {uniqueness}")
        if style_guidance:
            compact_base_parts.append(f"Style guidance: {style_guidance}")

        compact_base = ". ".join(part.rstrip(". ") for part in compact_base_parts if part).strip()
        raw_video_compact = _trim_video_prompt(raw_video)
        raw_image_compact = _trim_video_prompt(raw_image)
        base = compact_base or raw_video_compact or (_build_motion_prompt(raw_image_compact) if raw_image_compact else "")
        neighbor_notes = []
        previous_brief = _neighbor_brief(idx - 1)
        next_brief = _neighbor_brief(idx + 1)
        if previous_brief:
            neighbor_notes.append(f"avoid repeating the previous still scene ({previous_brief})")
        if next_brief:
            neighbor_notes.append(f"avoid repeating the next still scene ({next_brief})")
        differentiation = (
            "Distinct clip direction: start from the reference frame, then move into a clearly different animated beat, "
            "camera angle, depth, or subject action from the adjacent still images. "
            + ("; ".join(neighbor_notes) + ". " if neighbor_notes else "")
            + "End on a new composition, not a static duplicate of the neighboring scene."
        )
        if base:
            full = f"{vc_prefix}{base}. Camera: {camera_motion}. {differentiation}".strip()
            prompt_out = f"{full} Avoid: {negative}." if negative else full
            return _trim_sentence(prompt_out, 900)
        return (
            f"{vc_prefix}Animate this historical scene with natural cinematic motion, "
            f"dramatic documentary atmosphere, {camera_motion}. {differentiation}"
        ).strip()[:900]

    def _get_image(idx: int) -> Optional[Path]:
        return images[idx] if idx < len(images) else None

    # Define clips at narrative story beats rather than equal math splits.
    # Beat placement: opening (scene 1), rising action (~25%), midpoint (~50%),
    # late payoff (~75%). Using max(images, prompts) as the reference so
    # providers can still align clip placement when prompts and images differ.
    num_images = max(len(images), 1)
    _n = max(len(images), len(prompts), 1)
    opening_idx, q2_idx, q3_idx, q4_idx = _clip_target_indexes(_n)
    clip_targets = [
        ("opening", opening_idx, opening_idx, "ai_clip_opening.mp4"),
        ("q2", q2_idx, q2_idx, "ai_clip_q2.mp4"),
        ("q3", q3_idx, q3_idx, "ai_clip_q3.mp4"),
        ("q4", q4_idx, q4_idx, "ai_clip_q4.mp4"),
    ]

    results = []
    failures = []
    manifest: dict[str, object] = {
        "project_id": project_id,
        "provider": provider,
        "aspect_ratio": aspect_ratio,
        "duration_seconds": duration_seconds,
        "clips": [],
    }

    for label, img_idx, prompt_idx, out_name in clip_targets:
        image = _get_image(img_idx)
        prompt = _get_prompt(prompt_idx, label=label)
        packed_prompt = prompts[prompt_idx] if prompt_idx < len(prompts) and isinstance(prompts[prompt_idx], dict) else {}
        prompt_variants = _build_prompt_variants(
            base_prompt=prompt,
            label=label,
            packed=packed_prompt,
            aspect_ratio=aspect_ratio,
        )
        out_path = tmp_dir / out_name

        _wlog.info(
            "ai_video_clips generate provider=%s model=%s clip=%s prompt_len=%d image_path=%s",
            provider,
            str(get_secret("HF_GOOGLE_VIDEO_MODEL", DEFAULT_GOOGLE_VIDEO_MODEL) if provider == "google_veo_lite" else get_secret("fal_video_model", DEFAULT_FAL_VIDEO_MODEL)),
            label,
            len(prompt),
            str(image) if image else "",
        )

        reason = ""
        start = time.monotonic()
        scene_result: dict[str, object] = {}
        video_bytes = None
        try:
            if provider in ("falai", "google_veo_lite"):
                for attempt_idx, prompt_candidate in enumerate(prompt_variants, start=1):
                    scene_result = generate_scene_video(
                        provider=provider,
                        prompt=prompt_candidate,
                        image_path=str(image) if image else "",
                        aspect_ratio=aspect_ratio,
                        duration_seconds=duration_seconds,
                        output_path=str(out_path),
                        debug_dir=Path("data/projects") / project_id / "debug",
                    )
                    ok = bool(scene_result.get("ok"))
                    reason = str(scene_result.get("error") or "")
                    video_bytes = out_path.read_bytes() if ok and out_path.exists() else None
                    if video_bytes:
                        break
                    if provider == "google_veo_lite" and attempt_idx < len(prompt_variants):
                        _wlog.warning(
                            "ai_video_clips retry provider=%s clip=%s attempt=%d/%d reason=%s prompt_len=%d",
                            provider,
                            label,
                            attempt_idx,
                            len(prompt_variants),
                            reason or "empty response",
                            len(prompt_candidate),
                        )
                        time.sleep(1.0)
            else:
                raise ValueError(f"ai_video_clips: provider '{provider}' is no longer supported.")

        except RuntimeError:
            # Config / deployment error â€” re-raise so the pipeline surfaces it
            raise

        elapsed = time.monotonic() - start
        if video_bytes:
            if provider != "falai":
                out_path.write_bytes(video_bytes)
            _normalize_clip_orientation(out_path, _clip_w, _clip_h)
            _wlog.info(
                "ai_video_clips complete provider=%s clip=%s success=true output=%s bytes=%d elapsed=%.2fs",
                provider, label, out_path, len(video_bytes), elapsed,
            )
            results.append(out_path)
            manifest["clips"].append({"label": label, "status": "success", "path": str(out_path), "bytes": len(video_bytes), "elapsed_seconds": round(elapsed, 2)})
            if callable(clip_done_callback):
                try:
                    clip_done_callback(label, True, len(results), len(clip_targets))
                except Exception:  # noqa: BLE001
                    pass
        else:
            if not reason:
                reason = "provider returned empty response"
            _wlog.warning(
                "ai_video_clips complete provider=%s clip=%s success=false output=%s reason=%s elapsed=%.2fs",
                provider, label, out_path, reason, elapsed,
            )
            results.append(None)
            failures.append(label)
            manifest["clips"].append({"label": label, "status": "failed", "reason": reason, "elapsed_seconds": round(elapsed, 2)})
            if callable(clip_done_callback):
                try:
                    clip_done_callback(label, False, len(results), len(clip_targets))
                except Exception:  # noqa: BLE001
                    pass

    if failures:
        _wlog.warning(
            "ai_video_clips [%s]: %d/%d clips failed: %s",
            provider, len(failures), len(results), failures,
        )
    if all(r is None for r in results):
        _wlog.error(
            "ai_video_clips [%s]: ALL clips failed for project %s â€” check fal video response parsing, payload shape, async polling, or model compatibility",
            provider, project_id,
        )
    _clip_manifest_path(project_id).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return results[0], results[1], results[2], results[3]
