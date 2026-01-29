from __future__ import annotations

from dataclasses import dataclass

from .timeline_schema import Meta


@dataclass
class AudioMixPlan:
    input_args: list[str]
    filter_complex: str
    map_args: list[str]


def build_audio_mix_cmd(meta: Meta, total_duration: float) -> AudioMixPlan:
    input_args: list[str] = []
    filters: list[str] = []
    next_index = 0
    vo_label: str | None = None

    if meta.voiceover and meta.voiceover.path:
        input_args += ["-i", meta.voiceover.path]
        vo_input = f"[{next_index}:a]"
        next_index += 1
        if meta.voiceover.loudnorm:
            filters.append(
                f"{vo_input}loudnorm=I={meta.voiceover.target_i}:TP={meta.voiceover.true_peak}:LRA={meta.voiceover.lra}[vo]"
            )
        else:
            filters.append(f"{vo_input}anull[vo]")
        vo_label = "[vo]"

    music_label: str | None = None
    if meta.music and meta.music.path:
        input_args += ["-stream_loop", "-1", "-i", meta.music.path]
        music_input = f"[{next_index}:a]"
        next_index += 1
        filters.append(
            f"{music_input}atrim=0:{total_duration},asetpts=N/SR/TB,volume={meta.music.volume_db}dB[music]"
        )
        music_label = "[music]"

    if music_label and vo_label and meta.music and meta.music.ducking and meta.music.ducking.enabled:
        ducking = meta.music.ducking
        filters.append(
            f"{music_label}{vo_label}sidechaincompress=threshold={ducking.threshold_db}dB:ratio={ducking.ratio}:"
            f"attack={ducking.attack}:release={ducking.release}[ducked]"
        )
        music_label = "[ducked]"

    if music_label and vo_label:
        filters.append(f"{music_label}{vo_label}amix=inputs=2:dropout_transition=2[aout]")
    elif vo_label:
        filters.append(f"{vo_label}anull[aout]")
    elif music_label:
        filters.append(f"{music_label}anull[aout]")
    else:
        raise ValueError("No audio sources provided for mixing.")

    return AudioMixPlan(input_args=input_args, filter_complex=";".join(filters), map_args=["-map", "[aout]"])
