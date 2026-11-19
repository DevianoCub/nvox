"""WAV container parsing and sample-LSB extraction."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

_MAX_CHUNKS = 512
_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_IEEE_FLOAT = 0x0003
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


class WavError(ValueError):
    """Raised when a WAV payload cannot be parsed."""


@dataclass(frozen=True)
class WavChunk:
    """One RIFF chunk and its payload location."""

    id: str
    offset: int
    size: int
    data_offset: int


@dataclass(frozen=True)
class WavInfo:
    """Decoded RIFF/WAVE header and chunk table."""

    audio_format: int
    channels: int
    sample_rate: int
    byte_rate: int
    block_align: int
    bits_per_sample: int
    chunks: tuple[WavChunk, ...]
    data_offset: int | None
    data_size: int
    trailing_offset: int | None
    trailing_size: int = 0

    @property
    def sample_width(self) -> int:
        return max(1, (self.bits_per_sample + 7) // 8)

    @property
    def frame_count(self) -> int:
        if self.block_align <= 0:
            return 0
        return self.data_size // self.block_align

    @property
    def duration(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.frame_count / self.sample_rate

    @property
    def format_name(self) -> str:
        if self.audio_format == _WAVE_FORMAT_PCM:
            return "pcm"
        if self.audio_format == _WAVE_FORMAT_IEEE_FLOAT:
            return "float"
        if self.audio_format == _WAVE_FORMAT_EXTENSIBLE:
            return "extensible"
        return f"0x{self.audio_format:04x}"

    def metadata(self) -> dict[str, Any]:
        return {
            "format": self.format_name,
            "audio_format": self.audio_format,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "byte_rate": self.byte_rate,
            "block_align": self.block_align,
            "bits_per_sample": self.bits_per_sample,
            "sample_width": self.sample_width,
            "frames": self.frame_count,
            "duration_seconds": round(self.duration, 6),
            "data_offset": self.data_offset,
            "data_size": self.data_size,
            "trailing_bytes": self.trailing_size,
            "chunks": [
                {"id": chunk.id, "offset": chunk.offset, "size": chunk.size}
                for chunk in self.chunks
            ],
        }


def parse_wav(payload: bytes, *, max_chunks: int = _MAX_CHUNKS) -> WavInfo:
    """Parse a RIFF/WAVE payload without decoding sample data."""
    if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
        raise WavError("missing RIFF/WAVE header")
    declared_end = int.from_bytes(payload[4:8], "little") + 8
    if declared_end < 12:
        raise WavError("invalid RIFF size")
    end = min(len(payload), declared_end)
    offset = 12
    chunks: list[WavChunk] = []
    fmt: tuple[int, int, int, int, int, int] | None = None
    data_offset: int | None = None
    data_size = 0
    while offset + 8 <= end:
        if len(chunks) >= max_chunks:
            raise WavError(f"WAV has more than {max_chunks} chunks")
        chunk_id = payload[offset : offset + 4].decode("latin-1", errors="replace")
        size = int.from_bytes(payload[offset + 4 : offset + 8], "little")
        data_start = offset + 8
        data_end = data_start + size
        if data_end > end:
            raise WavError(f"truncated WAV chunk {chunk_id!r} at offset {offset}")
        chunks.append(WavChunk(chunk_id, offset, size, data_start))
        if chunk_id == "fmt ":
            if size < 16:
                raise WavError("fmt chunk is shorter than 16 bytes")
            fmt = struct.unpack("<HHIIHH", payload[data_start : data_start + 16])
            if fmt[0] == _WAVE_FORMAT_EXTENSIBLE and size >= 40:
                # The extensible sub-format GUID starts at byte 24. The first
                # two bytes carry the effective format tag.
                effective = int.from_bytes(payload[data_start + 24 : data_start + 26], "little")
                fmt = (effective, *fmt[1:])
        elif chunk_id == "data" and data_offset is None:
            data_offset = data_start
            data_size = size
        offset = data_end + (size & 1)

    if fmt is None:
        raise WavError("missing fmt chunk")
    audio_format, channels, sample_rate, byte_rate, block_align, bits = fmt
    if channels <= 0 or sample_rate <= 0 or block_align <= 0:
        raise WavError("invalid WAV format fields")
    if data_offset is None:
        raise WavError("missing data chunk")
    trailing_offset = None
    if declared_end < len(payload):
        trailing_offset = declared_end
    elif offset < len(payload):
        trailing_offset = offset
    trailing_size = 0 if trailing_offset is None else len(payload) - trailing_offset
    return WavInfo(
        audio_format=audio_format,
        channels=channels,
        sample_rate=sample_rate,
        byte_rate=byte_rate,
        block_align=block_align,
        bits_per_sample=bits,
        chunks=tuple(chunks),
        data_offset=data_offset,
        data_size=data_size,
        trailing_offset=trailing_offset,
        trailing_size=trailing_size,
    )


def extract_sample_lsb(
    payload: bytes,
    info: WavInfo,
    *,
    bit: int = 1,
    channels: str | int = "all",
    max_samples: int = 8_000_000,
    order: str = "msb",
) -> bytes:
    """Pack one bit from each PCM sample, one sample per bit.

    ``channels`` may be ``"all"`` (interleaved), an integer channel index, or a
    CSV/space-separated list. Only complete 8-bit bytes are emitted.
    """
    if info.data_offset is None or info.audio_format not in {
        _WAVE_FORMAT_PCM,
        _WAVE_FORMAT_EXTENSIBLE,
    }:
        raise WavError("sample LSB extraction needs PCM audio data")
    if info.bits_per_sample % 8:
        raise WavError("sample LSB extraction needs whole-byte samples")
    if bit < 1 or bit > info.bits_per_sample:
        raise WavError(f"bit must be between 1 and {info.bits_per_sample}")
    width = info.sample_width
    selected = _parse_channels(channels, info.channels)
    if not selected:
        raise WavError("no valid channels selected")
    if order not in {"msb", "lsb"}:
        raise WavError("order must be 'msb' or 'lsb'")
    frame_size = info.block_align
    if frame_size < width * info.channels:
        raise WavError("block_align is smaller than the sample data")
    total_frames = min(info.frame_count, max(1, max_samples) // len(selected))
    data = payload[info.data_offset : info.data_offset + info.data_size]
    bits: list[int] = []
    for frame in range(total_frames):
        base = frame * frame_size
        for channel in selected:
            start = base + channel * width
            if start + width > len(data):
                break
            value = int.from_bytes(data[start : start + width], "little")
            bits.append((value >> (bit - 1)) & 1)
    if not bits:
        return b""
    output = bytearray((len(bits) + 7) // 8)
    positions = tuple(range(7, -1, -1)) if order == "msb" else tuple(range(8))
    for index, value in enumerate(bits):
        byte_index, position = divmod(index, 8)
        output[byte_index] |= value << positions[position]
    return bytes(output)


def decode_pcm_samples(
    payload: bytes,
    info: WavInfo,
    *,
    max_frames: int = 8_000_000,
) -> list[float]:
    """Decode interleaved PCM samples to floats in roughly -1..1."""
    if info.data_offset is None:
        raise WavError("missing data chunk")
    if info.audio_format not in {_WAVE_FORMAT_PCM, _WAVE_FORMAT_EXTENSIBLE}:
        raise WavError("only integer PCM samples are supported")
    width = info.sample_width
    if info.bits_per_sample % 8 or width not in {1, 2, 3, 4}:
        raise WavError("unsupported PCM sample width")
    frames = min(info.frame_count, max(1, max_frames))
    data = payload[info.data_offset : info.data_offset + info.data_size]
    values: list[float] = []
    scale = float(1 << (info.bits_per_sample - 1))
    for frame in range(frames):
        base = frame * info.block_align
        for channel in range(info.channels):
            start = base + channel * width
            if start + width > len(data):
                break
            raw = int.from_bytes(data[start : start + width], "little")
            if width == 1:
                values.append((raw - 128) / 128.0)
            else:
                if raw & (1 << (info.bits_per_sample - 1)):
                    raw -= 1 << info.bits_per_sample
                values.append(raw / scale)
    return values


def mono_samples(values: list[float], channels: int) -> list[float]:
    """Average interleaved channels into a mono stream."""
    if channels <= 1:
        return values
    output: list[float] = []
    for index in range(0, len(values) - channels + 1, channels):
        output.append(sum(values[index : index + channels]) / channels)
    return output


def _parse_channels(value: str | int, available: int) -> tuple[int, ...]:
    if value == "all" or value is None:
        return tuple(range(available))
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
        selected = tuple(int(part) for part in parts)
    else:
        selected = (int(value),)
    return tuple(channel for channel in selected if 0 <= channel < available)


@register
class WavMetadataPlugin(Analyzer):
    """Report WAV chunks, format, duration and trailing data."""

    name: ClassVar[str] = "audio.wav"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("audio/wav", "audio/x-wav", "audio/wave")
    priority: ClassVar[int] = 30
    description: ClassVar[str] = "Parse WAV RIFF chunks, PCM format and trailing data."

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            info = parse_wav(artifact.payload)
        except WavError as exc:
            ctx.skip(f"wav: {exc}")
        ctx.meta("wav/metadata", info.metadata(), source="riff")
        if info.trailing_offset is not None:
            trailing = artifact.payload[info.trailing_offset :]
            if trailing:
                ctx.blob(
                    "wav/trailing",
                    trailing,
                    media_type="application/octet-stream",
                    offset=info.trailing_offset,
                    size=len(trailing),
                )


@register
class WavLsbPlugin(Analyzer):
    """Extract bit planes from WAV PCM sample values."""

    name: ClassVar[str] = "audio.wav_lsb"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("audio/wav", "audio/x-wav", "audio/wave")
    priority: ClassVar[int] = 35
    description: ClassVar[str] = "Extract WAV PCM sample LSB planes (optional multi-bit)."
    options: ClassVar[dict[str, object]] = {
        "bit": 1,
        "channels": "all",
        "order": "msb",
        "max_samples": 8_000_000,
    }

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            info = parse_wav(artifact.payload)
            bit = int(ctx.option("bit", 1))
            channels = ctx.option("channels", "all")
            order = str(ctx.option("order", "msb"))
            packed = extract_sample_lsb(
                artifact.payload,
                info,
                bit=bit,
                channels=channels,
                max_samples=int(ctx.option("max_samples", 8_000_000)),
                order=order,
            )
        except (WavError, TypeError, ValueError) as exc:
            ctx.skip(f"wav lsb: {exc}")
        if not packed:
            ctx.skip("wav lsb: no samples extracted")
        channel_label = (
            "all" if channels == "all" else "-".join(str(part) for part in _as_tuple(channels))
        )
        ctx.blob(
            f"wav/lsb/channel={channel_label}/bit={bit}",
            packed,
            media_type="application/octet-stream",
            bit=bit,
            channels=channels,
            sample_rate=info.sample_rate,
            bits_per_sample=info.bits_per_sample,
            order=order,
            frames=info.frame_count,
        )


def _as_tuple(value: object) -> tuple[object, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


__all__ = [
    "WavChunk",
    "WavError",
    "WavInfo",
    "WavLsbPlugin",
    "WavMetadataPlugin",
    "decode_pcm_samples",
    "extract_sample_lsb",
    "mono_samples",
    "parse_wav",
]
