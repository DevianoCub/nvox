"""DTMF, Morse and echo-hiding heuristics for PCM audio."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register
from nvox.plugins.audio.wav import (
    WavError,
    decode_pcm_samples,
    mono_samples,
    parse_wav,
)

_DTMF_ROWS = (697.0, 770.0, 852.0, 941.0)
_DTMF_COLUMNS = (1209.0, 1336.0, 1477.0, 1633.0)
_DTMF_KEYS = (
    ("1", "2", "3", "A"),
    ("4", "5", "6", "B"),
    ("7", "8", "9", "C"),
    ("*", "0", "#", "D"),
)

_MORSE = {
    ".-": "A",
    "-...": "B",
    "-.-.": "C",
    "-..": "D",
    ".": "E",
    "..-.": "F",
    "--.": "G",
    "....": "H",
    "..": "I",
    ".---": "J",
    "-.-": "K",
    ".-..": "L",
    "--": "M",
    "-.": "N",
    "---": "O",
    ".--.": "P",
    "--.-": "Q",
    ".-.": "R",
    "...": "S",
    "-": "T",
    "..-": "U",
    "...-": "V",
    ".--": "W",
    "-..-": "X",
    "-.--": "Y",
    "--..": "Z",
    "-----": "0",
    ".----": "1",
    "..---": "2",
    "...--": "3",
    "....-": "4",
    ".....": "5",
    "-....": "6",
    "--...": "7",
    "---..": "8",
    "----.": "9",
    ".-.-.-": ".",
    "--..--": ",",
    "..--..": "?",
    "-..-.": "/",
    "-....-": "-",
    ".--.-.": "@",
}


@dataclass(frozen=True)
class ToneHit:
    """One DTMF key press or Morse on/off interval."""

    value: str
    start: float
    duration: float


def _goertzel_power(block: list[float], frequency: float, sample_rate: int) -> float:
    omega = 2.0 * math.pi * frequency / sample_rate
    coefficient = 2.0 * math.cos(omega)
    s0 = s1 = s2 = 0.0
    for sample in block:
        s0 = sample + coefficient * s1 - s2
        s2 = s1
        s1 = s0
    power = s1 * s1 + s2 * s2 - coefficient * s1 * s2
    return max(0.0, power) / (len(block) * len(block))


def detect_dtmf(
    samples: list[float],
    sample_rate: int,
    *,
    frame_seconds: float = 0.04,
    min_frames: int = 2,
    threshold: float = 0.0005,
) -> tuple[ToneHit, ...]:
    """Detect DTMF key presses in mono audio."""
    frame_size = max(8, int(sample_rate * frame_seconds))
    hits: list[ToneHit] = []
    active_value: str | None = None
    active_start = 0
    active_frames = 0
    for start in range(0, len(samples) - frame_size + 1, frame_size):
        block = samples[start : start + frame_size]
        total = sum(value * value for value in block) / len(block)
        if total < 1e-6:
            low_index = high_index = -1
        else:
            row_powers = [
                _goertzel_power(block, frequency, sample_rate) for frequency in _DTMF_ROWS
            ]
            column_powers = [
                _goertzel_power(block, frequency, sample_rate) for frequency in _DTMF_COLUMNS
            ]
            low_power = max(row_powers)
            high_power = max(column_powers)
            low_index = row_powers.index(low_power)
            high_index = column_powers.index(high_power)
            if (
                low_power < threshold
                or high_power < threshold
                or low_power + high_power < total * 0.2
            ):
                low_index = high_index = -1
        value = _DTMF_KEYS[low_index][high_index] if low_index >= 0 else ""
        if value:
            if value == active_value:
                active_frames += 1
            else:
                if active_value is not None and active_frames >= min_frames:
                    hits.append(
                        ToneHit(
                            active_value,
                            active_start / sample_rate,
                            active_frames * frame_seconds,
                        )
                    )
                active_value = value
                active_start = start
                active_frames = 1
        elif active_value is not None:
            if active_frames >= min_frames:
                hits.append(
                    ToneHit(
                        active_value,
                        active_start / sample_rate,
                        active_frames * frame_seconds,
                    )
                )
            active_value = None
            active_frames = 0
    if active_value is not None and active_frames >= min_frames:
        hits.append(
            ToneHit(
                active_value,
                active_start / sample_rate,
                active_frames * frame_seconds,
            )
        )
    return tuple(hits)


def detect_morse(
    samples: list[float],
    sample_rate: int,
    *,
    block_seconds: float = 0.005,
    min_chars: int = 1,
) -> tuple[str, tuple[ToneHit, ...]]:
    """Decode on/off-keyed Morse from a mono waveform."""
    block_size = max(1, int(sample_rate * block_seconds))
    envelope: list[float] = []
    for start in range(0, len(samples) - block_size + 1, block_size):
        block = samples[start : start + block_size]
        envelope.append(max(abs(value) for value in block))
    peak = max(envelope, default=0.0)
    if peak < 0.01:
        return "", ()
    threshold = max(0.01, peak * 0.2)
    segments: list[tuple[bool, int, int]] = []
    state = envelope[0] >= threshold if envelope else False
    start_block = 0
    for index, value in enumerate(envelope[1:], start=1):
        current = value >= threshold
        if current != state:
            segments.append((state, start_block, index - start_block))
            state = current
            start_block = index
    if envelope:
        segments.append((state, start_block, len(envelope) - start_block))

    # A one-block spike is usually an edge artefact, not a Morse element.
    segments = [segment for segment in segments if segment[2] >= 2 or not segment[0]]
    on_durations = sorted(duration for active, _, duration in segments if active)
    if not on_durations:
        return "", ()
    unit_blocks = max(2, on_durations[0])
    events: list[ToneHit] = []
    output: list[str] = []
    letter: list[str] = []
    for active, start, duration in segments:
        seconds = duration * block_seconds
        if active:
            letter.append("." if duration < unit_blocks * 2 else "-")
        else:
            if duration >= unit_blocks * 2 and letter:
                output.append(_MORSE.get("".join(letter), "?"))
                letter = []
            if duration >= unit_blocks * 5:
                output.append(" ")
        events.append(ToneHit("on" if active else "off", start * block_seconds, seconds))
    if letter:
        output.append(_MORSE.get("".join(letter), "?"))
    text = "".join(output).strip()
    if sum(1 for char in text if char.isalnum()) < min_chars:
        return "", tuple(events)
    return text, tuple(events)


def autocorrelation_peaks(
    samples: list[float],
    *,
    min_lag: int = 20,
    max_lag: int = 1000,
    threshold: float = 0.25,
    max_samples: int = 20_000,
) -> tuple[dict[str, float], ...]:
    """Find strong autocorrelation peaks that may indicate echo hiding."""
    values = samples[: max(1, max_samples)]
    if len(values) < max_lag * 2:
        max_lag = max(min_lag + 1, len(values) // 2)
    if not values or max_lag <= min_lag:
        return ()
    mean = sum(values) / len(values)
    values = [value - mean for value in values]
    energy = sum(value * value for value in values)
    if energy <= 1e-12:
        return ()
    correlations: list[tuple[int, float]] = []
    for lag in range(min_lag, min(max_lag, len(values) - 1)):
        numerator = 0.0
        left_energy = 0.0
        right_energy = 0.0
        for index in range(len(values) - lag):
            left = values[index]
            right = values[index + lag]
            numerator += left * right
            left_energy += left * left
            right_energy += right * right
        if left_energy <= 1e-12 or right_energy <= 1e-12:
            continue
        correlation = numerator / math.sqrt(left_energy * right_energy)
        if correlation >= threshold:
            correlations.append((lag, correlation))
    peaks: list[dict[str, float]] = []
    for index, (lag, correlation) in enumerate(correlations):
        previous = correlations[index - 1][1] if index else -1.0
        following = correlations[index + 1][1] if index + 1 < len(correlations) else -1.0
        if correlation >= previous and correlation >= following:
            peaks.append({"lag_samples": lag, "correlation": round(correlation, 6)})
    peaks.sort(key=lambda item: item["correlation"], reverse=True)
    return tuple(peaks[:8])


@register
class DtmfMorsePlugin(Analyzer):
    """Decode DTMF digits and Morse hints from WAV audio."""

    name: ClassVar[str] = "audio.tones"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("audio/wav", "audio/x-wav", "audio/wave")
    priority: ClassVar[int] = 42
    description: ClassVar[str] = "Decode DTMF key presses and Morse hints from WAV audio."
    options: ClassVar[dict[str, object]] = {
        "max_samples": 4_000_000,
        "dtmf": True,
        "morse": True,
    }

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            info = parse_wav(artifact.payload)
            values = decode_pcm_samples(
                artifact.payload,
                info,
                max_frames=max(1, int(ctx.option("max_samples", 4_000_000))),
            )
            mono = mono_samples(values, info.channels)
        except (WavError, ValueError, TypeError) as exc:
            ctx.skip(f"tones: {exc}")
        dtmf_hits: tuple[ToneHit, ...] = ()
        morse_text = ""
        morse_events: tuple[ToneHit, ...] = ()
        if bool(ctx.option("dtmf", True)):
            dtmf_hits = detect_dtmf(mono, info.sample_rate)
        if bool(ctx.option("morse", True)):
            morse_text, morse_events = detect_morse(mono, info.sample_rate)
        if not dtmf_hits and not morse_text:
            ctx.skip("tones: no DTMF or Morse sequence detected")
        metadata: dict[str, Any] = {
            "sample_rate": info.sample_rate,
            "dtmf": [
                {
                    "value": hit.value,
                    "start": round(hit.start, 4),
                    "duration": round(hit.duration, 4),
                }
                for hit in dtmf_hits
            ],
            "morse": {
                "text": morse_text,
                "events": len(morse_events),
            },
        }
        ctx.meta("tones/metadata", metadata, source="dtmf-morse")
        if dtmf_hits:
            ctx.text("tones/dtmf", "".join(hit.value for hit in dtmf_hits), count=len(dtmf_hits))
        if morse_text:
            ctx.text("tones/morse", morse_text, events=len(morse_events))


@register
class EchoHidingPlugin(Analyzer):
    """Report autocorrelation peaks consistent with echo-hiding delays."""

    name: ClassVar[str] = "audio.echo"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("audio/wav", "audio/x-wav", "audio/wave")
    priority: ClassVar[int] = 48
    description: ClassVar[str] = "Look for echo-hiding autocorrelation peaks in WAV audio."
    options: ClassVar[dict[str, object]] = {
        "max_samples": 4_000_000,
        "min_lag": 20,
        "max_lag": 1000,
        "threshold": 0.25,
    }

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            info = parse_wav(artifact.payload)
            values = decode_pcm_samples(
                artifact.payload,
                info,
                max_frames=max(1, int(ctx.option("max_samples", 4_000_000))),
            )
            mono = mono_samples(values, info.channels)
        except (WavError, ValueError, TypeError) as exc:
            ctx.skip(f"echo: {exc}")
        peaks = autocorrelation_peaks(
            mono,
            min_lag=max(2, int(ctx.option("min_lag", 20))),
            max_lag=max(3, int(ctx.option("max_lag", 1000))),
            threshold=float(ctx.option("threshold", 0.25)),
        )
        if not peaks:
            ctx.skip("echo: no strong autocorrelation peaks")
        ctx.meta(
            "echo/peaks",
            {
                "sample_rate": info.sample_rate,
                "peaks": list(peaks),
                "method": "normalized autocorrelation",
            },
            source="echo-hiding",
        )


__all__ = [
    "DtmfMorsePlugin",
    "EchoHidingPlugin",
    "ToneHit",
    "autocorrelation_peaks",
    "detect_dtmf",
    "detect_morse",
]
