"""Cinematic video effects pipeline for History Forge.

Each function takes an input path (image or video clip) and writes an
effect-processed MP4 to the output path.  Functions are designed to be
chained: the output of one becomes the input of the next.

All effects use FFmpeg.  On failure each function logs the error and
returns ``False``; callers should fall back to the unprocessed clip
rather than aborting the whole render.

Output format: H.264 MP4, configurable resolution (default 1920×1080), 24 fps.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

from src.video.utils import get_media_duration, resolve_ffmpeg_exe, run_cmd

log = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_WIDTH: int = 1920
DEFAULT_HEIGHT: int = 1080
DEFAULT_FPS: int = 24
DEFAULT_CRF: int = 23
DEFAULT_PRESET: str = "veryfast"

PathLike = Union[str, Path]

VALID_KB_DIRECTIONS = frozenset({
    "zoom-in-center",
    "zoom-out-center",
    "pan-left-to-right",
    "pan-right-to-left",
    "pan-top-to-bottom",
})

VALID_GRADE_STYLES = frozenset({"warm", "cool", "neutral", "vintage"})
VALID_GRAIN_INTENSITIES = frozenset({"light", "medium", "heavy"})


# ── Internal helpers ───────────────────────────────────────────────────────────

def _ffmpeg_exe() -> str:
    try:
        return resolve_ffmpeg_exe()
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg not found; install ffmpeg or add it to PATH.") from exc


def _run(cmd: list[str], label: str, workdir: Optional[Path] = None) -> bool:
    """Execute an FFmpeg command; return True on success."""
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="effects_"))
    try:
        result = run_cmd(cmd, check=False, workdir=workdir)
        if not result.get("ok"):
            stderr_tail = (result.get("stderr") or "")[-600:]
            log.error("[effects] %s failed (rc=%s):\n%s", label, result.get("returncode"), stderr_tail)
            return False
        return True
    except Exception as exc:
        log.exception("[effects] %s raised an exception: %s", label, exc)
        return False


def _ensure_out(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)


# ── Public effect functions ────────────────────────────────────────────────────

def ken_burns(
    image_path: PathLike,
    output_path: PathLike,
    duration: float = 5.0,
    direction: str = "zoom-in-center",
    zoom_factor: float = 1.15,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> bool:
    """Animate a static image with a pan/zoom (Ken Burns) effect.

    The image is first scaled to fill the target frame, then the zoompan
    filter applies the chosen motion over the clip duration.

    Args:
        image_path:  Source image (PNG, JPG, WEBP, …).
        output_path: Destination MP4 path (H.264, no audio).
        duration:    Clip length in seconds.
        direction:   Motion type.  One of:
                       ``zoom-in-center``   – zoom toward image centre
                       ``zoom-out-center``  – zoom away from centre
                       ``pan-left-to-right``
                       ``pan-right-to-left``
                       ``pan-top-to-bottom``
        zoom_factor: Maximum zoom level (1.0 = no zoom, 1.2 = 20% crop).
        width:       Output frame width in pixels.
        height:      Output frame height in pixels.
        fps:         Frames per second.

    Returns:
        ``True`` if the clip was created successfully, ``False`` otherwise.
    """
    image_path = Path(image_path)
    output_path = Path(output_path)
    _ensure_out(output_path)

    if not image_path.exists():
        log.error("[effects] ken_burns: image not found: %s", image_path)
        return False

    if direction not in VALID_KB_DIRECTIONS:
        log.warning("[effects] ken_burns: unknown direction %r, using zoom-in-center", direction)
        direction = "zoom-in-center"

    zf = max(1.001, float(zoom_factor))
    # Render zoompan at 3× the target FPS internally so position is computed at
    # triple the resolution, then downsample with the fps filter.  This triples
    # the number of intermediate steps (a 200% smoothness increase) and
    # eliminates the sub-pixel jitter that plagues low-fps zoompan renders.
    internal_fps = fps * 3
    total_frames = max(2, int(duration * internal_fps))
    step = (zf - 1.0) / total_frames

    # Build zoompan z/x/y expressions.
    # Input to zoompan = width×height (after scale+crop below).
    # (iw-iw/zoom)/2  centres the crop window as zoom changes.
    if direction == "zoom-in-center":
        z_expr = f"min(zoom+{step:.7f},{zf:.5f})"
        x_expr = "(iw-iw/zoom)/2"
        y_expr = "(ih-ih/zoom)/2"

    elif direction == "zoom-out-center":
        # Initial zoom == 1 in zoompan; seed to zf on first frame.
        z_expr = f"if(eq(on,0),{zf:.5f},max(zoom-{step:.7f},1.0))"
        x_expr = "(iw-iw/zoom)/2"
        y_expr = "(ih-ih/zoom)/2"

    elif direction == "pan-left-to-right":
        z_expr = f"{zf:.5f}"
        x_expr = f"(iw-iw/{zf:.5f})*on/{total_frames}"
        y_expr = f"(ih-ih/{zf:.5f})/2"

    elif direction == "pan-right-to-left":
        z_expr = f"{zf:.5f}"
        x_expr = f"(iw-iw/{zf:.5f})*(1-on/{total_frames})"
        y_expr = f"(ih-ih/{zf:.5f})/2"

    else:  # pan-top-to-bottom
        z_expr = f"{zf:.5f}"
        x_expr = f"(iw-iw/{zf:.5f})/2"
        y_expr = f"(ih-ih/{zf:.5f})*on/{total_frames}"

    zoompan = (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
        f":d={total_frames}:s={width}x{height}:fps={internal_fps}"
    )
    # Scale image to exact output size first so zoompan pixel coords are
    # predictable; crop removes any letterbox/pillarbox from aspect mismatch.
    # fps={fps} downsamples from the 3× internal rate back to the target rate.
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"{zoompan},"
        f"fps={fps},"
        f"format=yuv420p"
    )

    cmd = [
        _ffmpeg_exe(),
        "-loop", "1",
        "-framerate", str(fps),
        "-i", str(image_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", DEFAULT_PRESET,
        "-crf", str(DEFAULT_CRF),
        "-r", str(fps),
        "-t", f"{duration:.3f}",
        "-an",
        "-y",
        str(output_path),
    ]
    return _run(cmd, f"ken_burns({direction})")


def fade_in_out(
    clip_path: PathLike,
    output_path: PathLike,
    fade_in_duration: float = 0.5,
    fade_out_duration: float = 0.5,
) -> bool:
    """Add a fade-in from black and fade-out to black on a video clip.

    Args:
        clip_path:         Source MP4 (or any video FFmpeg can decode).
        output_path:       Destination MP4 path.
        fade_in_duration:  Seconds to fade in from black (0 = no fade-in).
        fade_out_duration: Seconds to fade out to black (0 = no fade-out).

    Returns:
        ``True`` on success.
    """
    clip_path = Path(clip_path)
    output_path = Path(output_path)
    _ensure_out(output_path)

    if not clip_path.exists():
        log.error("[effects] fade_in_out: clip not found: %s", clip_path)
        return False

    total_dur = get_media_duration(clip_path)
    if total_dur <= 0:
        log.error("[effects] fade_in_out: could not read duration of %s", clip_path)
        return False

    # Clamp fade durations so they don't overlap or exceed the clip.
    fi = max(0.0, min(float(fade_in_duration), total_dur / 2.0))
    fo = max(0.0, min(float(fade_out_duration), total_dur / 2.0))

    filters: list[str] = []
    if fi > 0:
        filters.append(f"fade=t=in:st=0:d={fi:.3f}")
    if fo > 0:
        start_out = total_dur - fo
        filters.append(f"fade=t=out:st={start_out:.3f}:d={fo:.3f}")

    if not filters:
        # Nothing to do – just copy.
        cmd = [
            _ffmpeg_exe(), "-i", str(clip_path),
            "-c", "copy", "-y", str(output_path),
        ]
    else:
        vf = ",".join(filters)
        cmd = [
            _ffmpeg_exe(),
            "-i", str(clip_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", DEFAULT_PRESET,
            "-crf", str(DEFAULT_CRF),
            "-c:a", "copy",
            "-y",
            str(output_path),
        ]
    return _run(cmd, "fade_in_out")


def color_grade(
    clip_path: PathLike,
    output_path: PathLike,
    style: str = "warm",
) -> bool:
    """Apply a cinematic colour-grade look to a video clip.

    Args:
        clip_path:   Source MP4.
        output_path: Destination MP4.
        style:       One of ``"warm"`` (golden/sepia), ``"cool"``
                     (desaturated blue), ``"neutral"`` (subtle contrast
                     boost), ``"vintage"`` (faded low-contrast look).

    Returns:
        ``True`` on success.
    """
    clip_path = Path(clip_path)
    output_path = Path(output_path)
    _ensure_out(output_path)

    if not clip_path.exists():
        log.error("[effects] color_grade: clip not found: %s", clip_path)
        return False

    if style not in VALID_GRADE_STYLES:
        log.warning("[effects] color_grade: unknown style %r, using neutral", style)
        style = "neutral"

    # FFmpeg curves points: "input/output" pairs in [0,1].
    STYLE_FILTERS: dict[str, str] = {
        # Warm: boost reds, lift greens slightly, pull blues down → golden tone.
        "warm": (
            "curves="
            "r='0/0 0.25/0.30 0.75/0.82 1/1':"
            "g='0/0 0.25/0.26 0.75/0.77 1/0.95':"
            "b='0/0 0.25/0.20 0.75/0.65 1/0.82',"
            "eq=saturation=1.15:brightness=0.02:contrast=1.05"
        ),
        # Cool: pull reds down, lift blues → desaturated cold look.
        "cool": (
            "curves="
            "r='0/0 0.25/0.22 0.75/0.68 1/0.88':"
            "g='0/0 0.25/0.25 0.75/0.76 1/0.97':"
            "b='0/0 0.25/0.28 0.75/0.80 1/1',"
            "eq=saturation=0.80:contrast=1.05"
        ),
        # Neutral: clean contrast boost with a touch of brightness.
        "neutral": (
            "eq=contrast=1.10:brightness=0.02:saturation=1.05:gamma=0.96"
        ),
        # Vintage: lifted blacks, pulled whites, reduced saturation → faded look.
        "vintage": (
            "curves="
            "r='0/0.06 0.25/0.28 0.75/0.70 1/0.94':"
            "g='0/0.05 0.25/0.27 0.75/0.68 1/0.90':"
            "b='0/0.08 0.25/0.25 0.75/0.62 1/0.86',"
            "eq=saturation=0.75:contrast=0.90:brightness=0.01"
        ),
    }

    vf = STYLE_FILTERS[style] + ",format=yuv420p"
    cmd = [
        _ffmpeg_exe(),
        "-i", str(clip_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", DEFAULT_PRESET,
        "-crf", str(DEFAULT_CRF),
        "-c:a", "copy",
        "-y",
        str(output_path),
    ]
    return _run(cmd, f"color_grade({style})")


def film_grain(
    clip_path: PathLike,
    output_path: PathLike,
    intensity: str = "medium",
) -> bool:
    """Overlay vintage film-grain texture on a video clip.

    Grain is temporally randomised (different pattern each frame) and
    applied to all colour channels, simulating photochemical film stock.
    A very subtle per-frame brightness flicker is added for medium and
    heavy intensities.

    Args:
        clip_path:   Source MP4.
        output_path: Destination MP4.
        intensity:   ``"light"``, ``"medium"``, or ``"heavy"``.

    Returns:
        ``True`` on success.
    """
    clip_path = Path(clip_path)
    output_path = Path(output_path)
    _ensure_out(output_path)

    if not clip_path.exists():
        log.error("[effects] film_grain: clip not found: %s", clip_path)
        return False

    if intensity not in VALID_GRAIN_INTENSITIES:
        log.warning("[effects] film_grain: unknown intensity %r, using medium", intensity)
        intensity = "medium"

    # noise filter: alls = luma+chroma strength, allf = t (temporal) + u (uniform)
    # Temporal noise means each frame has a new random pattern – essential for
    # the flickering grain look rather than a static texture.
    GRAIN: dict[str, tuple[int, float]] = {
        #                     noise_strength, flicker_amplitude
        "light":   (7,  0.0),
        "medium":  (16, 0.018),
        "heavy":   (30, 0.035),
    }
    strength, flicker = GRAIN[intensity]

    noise_filter = f"noise=alls={strength}:allf=t+u"

    if flicker > 0:
        # geq applies a per-pixel expression; using 'p(X,Y)*brightness_factor'
        # where the factor oscillates slightly per frame to simulate projector
        # flicker.  'random(0)' in geq returns a per-frame pseudo-random value.
        flicker_filter = (
            f"geq="
            f"lum='p(X,Y)*(1+{flicker:.4f}*(random(0)-0.5))':"
            f"cb='p(X,Y)':"
            f"cr='p(X,Y)'"
        )
        vf = f"{noise_filter},{flicker_filter},format=yuv420p"
    else:
        vf = f"{noise_filter},format=yuv420p"

    cmd = [
        _ffmpeg_exe(),
        "-i", str(clip_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", DEFAULT_PRESET,
        "-crf", str(DEFAULT_CRF),
        "-c:a", "copy",
        "-y",
        str(output_path),
    ]
    return _run(cmd, f"film_grain({intensity})")


def map_flyover(
    image_path: PathLike,
    output_path: PathLike,
    duration: float = 6.0,
    start_coords: tuple[float, float] = (0.5, 0.5),
    end_coords: tuple[float, float] = (0.5, 0.5),
    zoom_factor: float = 2.5,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> bool:
    """Animate a map image with a smooth zoom-into-location flyover.

    The camera starts at a wide view centred on *start_coords* and smoothly
    zooms in to *zoom_factor* while panning to *end_coords*.

    Args:
        image_path:   Source map image.
        output_path:  Destination MP4 (H.264, no audio).
        duration:     Clip duration in seconds.
        start_coords: (x, y) fractional position [0.0–1.0] for the wide
                      starting view centre.
        end_coords:   (x, y) fractional position for the zoomed-in centre.
        zoom_factor:  Final zoom level (e.g. 2.5 = 2.5× magnification).
        width:        Output frame width.
        height:       Output frame height.
        fps:          Frames per second.

    Returns:
        ``True`` on success.
    """
    image_path = Path(image_path)
    output_path = Path(output_path)
    _ensure_out(output_path)

    if not image_path.exists():
        log.error("[effects] map_flyover: image not found: %s", image_path)
        return False

    zf = max(1.001, float(zoom_factor))
    # Render at 3× FPS internally for 200% smoother motion (see ken_burns).
    internal_fps = fps * 3
    total_frames = max(2, int(duration * internal_fps))

    sx, sy = float(start_coords[0]), float(start_coords[1])
    ex, ey = float(end_coords[0]), float(end_coords[1])

    # Clamp fractional coords to [0,1].
    sx, sy = max(0.0, min(1.0, sx)), max(0.0, min(1.0, sy))
    ex, ey = max(0.0, min(1.0, ex)), max(0.0, min(1.0, ey))

    # Zoom ramps from 1.0 → zf over total_frames.
    step = (zf - 1.0) / total_frames
    z_expr = f"min(zoom+{step:.7f},{zf:.5f})"

    # Centre of the visible crop window (in input pixels) interpolates
    # linearly from start_coords to end_coords as zoom increases.
    # At frame `on`: t = on/total_frames
    #   cx = (sx + (ex-sx)*t) * iw
    #   cy = (sy + (ey-sy)*t) * ih
    #   x  = cx - (crop_w)/2  where crop_w = iw/zoom
    #        = cx - iw/(2*zoom)
    dx, dy = ex - sx, ey - sy
    cx_expr = f"(({sx:.5f}+{dx:.5f}*on/{total_frames})*iw)"
    cy_expr = f"(({sy:.5f}+{dy:.5f}*on/{total_frames})*ih)"

    # Use zoom (current z value) for crop half-size.
    x_expr = f"max(0,min({cx_expr}-iw/(2*zoom),iw-iw/zoom))"
    y_expr = f"max(0,min({cy_expr}-ih/(2*zoom),ih-ih/zoom))"

    zoompan = (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
        f":d={total_frames}:s={width}x{height}:fps={internal_fps}"
    )
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"{zoompan},"
        f"fps={fps},"
        f"format=yuv420p"
    )

    cmd = [
        _ffmpeg_exe(),
        "-loop", "1",
        "-framerate", str(fps),
        "-i", str(image_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", DEFAULT_PRESET,
        "-crf", str(DEFAULT_CRF),
        "-r", str(fps),
        "-t", f"{duration:.3f}",
        "-an",
        "-y",
        str(output_path),
    ]
    return _run(cmd, "map_flyover")


# ── Effect chain helper ────────────────────────────────────────────────────────

def apply_effects_chain(
    image_path: PathLike,
    output_path: PathLike,
    *,
    # Ken Burns / map flyover
    ken_burns_enabled: bool = True,
    ken_burns_direction: str = "zoom-in-center",
    ken_burns_duration: float = 5.0,
    ken_burns_zoom_factor: float = 1.15,
    is_map_image: bool = False,
    map_start_coords: tuple[float, float] = (0.5, 0.5),
    map_end_coords: tuple[float, float] = (0.5, 0.5),
    map_zoom_factor: float = 2.5,
    # Fade
    fade_enabled: bool = True,
    fade_in_duration: float = 0.4,
    fade_out_duration: float = 0.4,
    # Colour grade
    color_grade_enabled: bool = True,
    color_grade_style: str = "warm",
    # Film grain
    film_grain_enabled: bool = True,
    film_grain_intensity: str = "medium",
    # Output
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
    workdir: Optional[PathLike] = None,
) -> bool:
    """Apply a full cinematic effects chain to a single image.

    Effects are applied in order:
      1. Ken Burns *or* Map Flyover  → base video clip
      2. Fade in / out
      3. Colour grade
      4. Film grain

    Each step writes to a temporary file; only the final output_path is kept.
    On any step failure the partial result from the previous step is used as
    the fallback, so the chain degrades gracefully.

    Args:
        image_path:  Source image.
        output_path: Destination MP4 (final, all effects applied).
        *:           Per-effect enable flags and parameters (see individual
                     effect functions for parameter descriptions).
        workdir:     Optional directory for intermediate temp files.  A
                     system temp directory is used when not provided.

    Returns:
        ``True`` if at least the base clip (step 1) succeeded.
    """
    image_path = Path(image_path)
    output_path = Path(output_path)

    if workdir is not None:
        tmp_dir = Path(workdir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        _cleanup = False
    else:
        import tempfile as _tempfile
        tmp_dir = Path(_tempfile.mkdtemp(prefix="hf_effects_"))
        _cleanup = True

    stem = image_path.stem

    def _tmp(suffix: str) -> Path:
        return tmp_dir / f"{stem}_{suffix}.mp4"

    # ── Step 1: motion (Ken Burns or Map Flyover) ───────────────────────────
    base_clip = _tmp("motion")
    if is_map_image and ken_burns_enabled:
        ok = map_flyover(
            image_path, base_clip,
            duration=ken_burns_duration,
            start_coords=map_start_coords,
            end_coords=map_end_coords,
            zoom_factor=map_zoom_factor,
            width=width, height=height, fps=fps,
        )
    elif ken_burns_enabled:
        ok = ken_burns(
            image_path, base_clip,
            duration=ken_burns_duration,
            direction=ken_burns_direction,
            zoom_factor=ken_burns_zoom_factor,
            width=width, height=height, fps=fps,
        )
    else:
        # Just encode the static image as a video loop.
        ok = ken_burns(
            image_path, base_clip,
            duration=ken_burns_duration,
            direction="zoom-in-center",
            zoom_factor=1.001,  # imperceptible zoom to keep clip valid
            width=width, height=height, fps=fps,
        )

    if not ok:
        log.error("[effects] apply_effects_chain: base clip generation failed for %s", image_path)
        return False

    current = base_clip

    # ── Step 2: fade in / out ───────────────────────────────────────────────
    if fade_enabled and (fade_in_duration > 0 or fade_out_duration > 0):
        fade_out_clip = _tmp("fade")
        ok = fade_in_out(
            current, fade_out_clip,
            fade_in_duration=fade_in_duration,
            fade_out_duration=fade_out_duration,
        )
        if ok:
            current = fade_out_clip
        else:
            log.warning("[effects] fade failed for %s; continuing without fade", image_path.name)

    # ── Step 3: colour grade ────────────────────────────────────────────────
    if color_grade_enabled:
        graded_clip = _tmp("grade")
        ok = color_grade(current, graded_clip, style=color_grade_style)
        if ok:
            current = graded_clip
        else:
            log.warning("[effects] color_grade failed for %s; continuing without grade", image_path.name)

    # ── Step 4: film grain ──────────────────────────────────────────────────
    if film_grain_enabled:
        grained_clip = _tmp("grain")
        ok = film_grain(current, grained_clip, intensity=film_grain_intensity)
        if ok:
            current = grained_clip
        else:
            log.warning("[effects] film_grain failed for %s; continuing without grain", image_path.name)

    # Move the best result to the requested output path.
    import shutil
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, output_path)
    except OSError as exc:
        log.error("[effects] failed to copy result to %s: %s", output_path, exc)
        return False
    finally:
        if _cleanup:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return True
