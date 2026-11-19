"""WAV spectrogram rendering with a pure-Python radix-2 FFT."""

from __future__ import annotations

import cmath
import math
from collections.abc import Sequence
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register
from nvox.plugins.audio.wav import (
    WavError,
    decode_pcm_samples,
    mono_samples,
    parse_wav,
)
from nvox.plugins.image.png import encode_gray_png


def _next_power_of_two(value: int) -> int:
    size = 1
    while size < value:
        size <<= 1
    return size


def fft(values: Sequence[float | complex]) -> list[complex]:
    """Return the discrete Fourier transform of a power-of-two sequence."""
    size = len(values)
    if size <= 1:
        return [complex(value) for value in values]
    if size & (size - 1):
        raise ValueError("FFT length must be a power of two")
    data = [complex(value) for value in values]
    # Bit-reversal permutation.
    bits = size.bit_length() - 1
    for index in range(size):
        reversed_index = int(f"{index:0{bits}b}"[::-1], 2) if bits else 0
        if index < reversed_index:
            data[index], data[reversed_index] = data[reversed_index], data[index]
    length = 2
    while length <= size:
        angle = -2.0 * math.pi / length
        half = length // 2
        twiddles = [cmath.exp(1j * angle * index) for index in range(half)]
        for start in range(0, size, length):
            for offset in range(half):
                left = data[start + offset]
                right = data[start + offset + half] * twiddles[offset]
                data[start + offset] = left + right
                data[start + offset + half] = left - right
        length <<= 1
    return data


def spectrogram(
    samples: list[float],
    sample_rate: int,
    *,
    fft_size: int = 1024,
    hop: int = 512,
    max_frames: int = 2000,
    dynamic_range_db: float = 80.0,
) -> tuple[int, int, bytes, dict[str, Any]]:
    """Render mono samples as a grayscale spectrogram.

    The returned image is ``width x height`` where each column is one FFT frame
    and the lowest frequencies are at the bottom of the image.
    """
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    if fft_size <= 0 or hop <= 0:
        raise ValueError("fft_size and hop must be positive")
    fft_size = _next_power_of_two(fft_size)
    if len(samples) < fft_size:
        raise ValueError(f"need at least {fft_size} samples for one FFT frame")
    window = [
        0.5 - 0.5 * math.cos(2.0 * math.pi * index / (fft_size - 1)) for index in range(fft_size)
    ]
    frame_count = min(max_frames, 1 + (len(samples) - fft_size) // hop)
    if frame_count <= 0:
        raise ValueError("no spectrogram frames fit the sample data")
    height = fft_size // 2
    magnitudes: list[list[float]] = []
    for frame_index in range(frame_count):
        start = frame_index * hop
        block = [samples[start + index] * window[index] for index in range(fft_size)]
        spectrum = fft(block)
        magnitudes.append([abs(spectrum[index]) for index in range(height)])
    peak = max((max(column, default=0.0) for column in magnitudes), default=0.0)
    if peak <= 0:
        raise ValueError("audio is silent; spectrogram would be empty")
    floor = peak * (10.0 ** (-dynamic_range_db / 20.0))
    pixels = bytearray(height * frame_count)
    for x, column in enumerate(magnitudes):
        for y, magnitude in enumerate(column):
            clipped = max(floor, min(peak, magnitude))
            if peak == floor:
                value = 0
            else:
                normalized = math.log10(clipped / floor) / math.log10(peak / floor)
                value = max(0, min(255, round(normalized * 255.0)))
            row = height - 1 - y
            pixels[row * frame_count + x] = value
    metadata = {
        "width": frame_count,
        "height": height,
        "fft_size": fft_size,
        "hop": hop,
        "sample_rate": sample_rate,
        "max_frequency": sample_rate / 2,
        "frequency_resolution": sample_rate / fft_size,
        "duration_seconds": len(samples) / sample_rate,
        "dynamic_range_db": dynamic_range_db,
    }
    return frame_count, height, bytes(pixels), metadata


@register
class SpectrogramPlugin(Analyzer):
    """Render WAV audio as a grayscale spectrogram image."""

    name: ClassVar[str] = "audio.spectrogram"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("audio/wav", "audio/x-wav", "audio/wave")
    priority: ClassVar[int] = 40
    description: ClassVar[str] = (
        "Render WAV audio as a spectrogram image for visual and OCR analysis."
    )
    options: ClassVar[dict[str, object]] = {
        "fft_size": 1024,
        "hop": 512,
        "max_frames": 2000,
        "max_seconds": 120.0,
        "max_samples": 8_000_000,
        "dynamic_range_db": 80.0,
    }

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            info = parse_wav(artifact.payload)
            max_samples = max(1, int(ctx.option("max_samples", 8_000_000)))
            values = decode_pcm_samples(artifact.payload, info, max_frames=max_samples)
            mono = mono_samples(values, info.channels)
            max_seconds = max(0.1, float(ctx.option("max_seconds", 120.0)))
            max_frames = int(max_seconds * info.sample_rate)
            if len(mono) > max_frames:
                mono = mono[:max_frames]
            width, height, pixels, metadata = spectrogram(
                mono,
                info.sample_rate,
                fft_size=int(ctx.option("fft_size", 1024)),
                hop=int(ctx.option("hop", 512)),
                max_frames=max(1, int(ctx.option("max_frames", 2000))),
                dynamic_range_db=float(ctx.option("dynamic_range_db", 80.0)),
            )
        except (WavError, ValueError, TypeError) as exc:
            ctx.skip(f"spectrogram: {exc}")
        image = encode_gray_png(width, height, pixels)
        ctx.image(
            "spectrogram/spectrogram.png",
            image,
            media_type="image/png",
            source="spectrogram",
            **metadata,
        )
        ctx.meta("spectrogram/meta", metadata, source="spectrogram")


__all__ = ["SpectrogramPlugin", "fft", "spectrogram"]
