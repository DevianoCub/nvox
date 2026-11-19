"""MP3/ID3 metadata and MPEG audio frame anomaly analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

_ID3V1_GENRES = (
    "Blues",
    "Classic Rock",
    "Country",
    "Dance",
    "Disco",
    "Funk",
    "Grunge",
    "Hip-Hop",
    "Jazz",
    "Metal",
    "New Age",
    "Oldies",
    "Other",
    "Pop",
    "R&B",
    "Rap",
    "Reggae",
    "Rock",
    "Techno",
    "Industrial",
    "Alternative",
    "Ska",
    "Death Metal",
    "Pranks",
    "Soundtrack",
    "Euro-Techno",
    "Ambient",
    "Trip-Hop",
    "Vocal",
    "Jazz+Funk",
    "Fusion",
    "Trance",
    "Classical",
    "Instrumental",
    "Acid",
    "House",
    "Game",
    "Sound Clip",
    "Gospel",
    "Noise",
    "AlternRock",
    "Bass",
    "Soul",
    "Punk",
    "Space",
    "Meditative",
    "Instrumental Pop",
    "Instrumental Rock",
    "Ethnic",
    "Gothic",
    "Darkwave",
    "Techno-Industrial",
    "Electronic",
    "Pop-Folk",
    "Eurodance",
    "Dream",
    "Southern Rock",
    "Comedy",
    "Cult",
    "Gangsta",
    "Top 40",
    "Christian Rap",
    "Pop/Funk",
    "Jungle",
    "Native American",
    "Cabaret",
    "New Wave",
    "Psychadelic",
    "Rave",
    "Showtunes",
    "Trailer",
    "Lo-Fi",
    "Tribal",
    "Acid Punk",
    "Acid Jazz",
    "Polka",
    "Retro",
    "Musical",
    "Rock & Roll",
    "Hard Rock",
)

_BITRATES: dict[tuple[int, int], tuple[int, ...]] = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES: dict[int, tuple[int, ...]] = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),
}
_LAYER_NAMES = {1: "I", 2: "II", 3: "III"}
_VERSION_NAMES = {3: "1", 2: "2", 0: "2.5"}
_CHANNEL_MODES = {"stereo", "joint stereo", "dual channel", "mono"}


class Mp3Error(ValueError):
    """Raised when an MP3/ID3 payload cannot be parsed."""


@dataclass(frozen=True)
class Id3Frame:
    """One ID3v2 frame with its decoded text values."""

    frame_id: str
    size: int
    encoding: int
    values: tuple[str, ...] = ()
    description: str = ""
    media_type: str = ""
    flags: int = 0


@dataclass(frozen=True)
class Id3Info:
    """Parsed ID3v2 and ID3v1 tags."""

    version: str
    revision: int
    flags: int
    size: int
    frames: tuple[Id3Frame, ...]
    id3v1: dict[str, str]
    padding: int
    end_offset: int

    def texts(self) -> dict[str, list[str]]:
        values: dict[str, list[str]] = {}
        for frame in self.frames:
            if frame.values:
                values.setdefault(frame.frame_id, []).extend(frame.values)
        for key, value in self.id3v1.items():
            if value:
                values.setdefault(f"ID3v1:{key}", []).append(value)
        return values

    def metadata(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "revision": self.revision,
            "flags": self.flags,
            "size": self.size,
            "end_offset": self.end_offset,
            "padding": self.padding,
            "frames": [
                {
                    "id": frame.frame_id,
                    "size": frame.size,
                    "encoding": frame.encoding,
                    "values": list(frame.values),
                    "description": frame.description,
                    "media_type": frame.media_type,
                    "flags": frame.flags,
                }
                for frame in self.frames
            ],
            "id3v1": dict(self.id3v1),
        }


@dataclass(frozen=True)
class MpegFrame:
    """One MPEG audio frame header."""

    offset: int
    length: int
    version: int
    layer: int
    bitrate: int
    sample_rate: int
    padding: int
    channels: str

    @property
    def duration(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        if self.layer == 1:
            samples = 384
        elif self.version == 3:
            samples = 1152
        else:
            samples = 576
        return samples / self.sample_rate


@dataclass(frozen=True)
class MpegScan:
    """Result of walking MPEG audio frames."""

    frames: tuple[MpegFrame, ...]
    first_offset: int
    last_end: int
    invalid_headers: int
    sync_losses: int
    truncated_frames: int
    inconsistent_frames: int
    trailing_offset: int | None
    trailing_size: int

    @property
    def duration(self) -> float:
        return sum(frame.duration for frame in self.frames)

    def metadata(self) -> dict[str, Any]:
        if not self.frames:
            summary: dict[str, Any] = {}
        else:
            first = self.frames[0]
            summary = {
                "version": _VERSION_NAMES.get(first.version, str(first.version)),
                "layer": _LAYER_NAMES.get(first.layer, str(first.layer)),
                "bitrate_kbps": first.bitrate,
                "sample_rate": first.sample_rate,
                "channel_mode": first.channels,
            }
        return {
            **summary,
            "frames": len(self.frames),
            "duration_seconds": round(self.duration, 6),
            "first_offset": self.first_offset,
            "last_end": self.last_end,
            "invalid_headers": self.invalid_headers,
            "sync_losses": self.sync_losses,
            "truncated_frames": self.truncated_frames,
            "inconsistent_frames": self.inconsistent_frames,
            "trailing_offset": self.trailing_offset,
            "trailing_bytes": self.trailing_size,
        }

    @property
    def anomalous(self) -> bool:
        return any(
            (
                not self.frames,
                self.invalid_headers > 0,
                self.sync_losses > 0,
                self.truncated_frames > 0,
                self.inconsistent_frames > 0,
                self.trailing_size > 0,
            )
        )


def parse_id3(payload: bytes, *, max_frames: int = 256) -> Id3Info | None:
    """Parse ID3v2.2/2.3/2.4 and the trailing ID3v1 tag if present."""
    if not payload.startswith(b"ID3"):
        id3v1 = _parse_id3v1(payload)
        if not id3v1:
            return None
        return Id3Info("", 0, 0, 0, (), id3v1, 0, len(payload))
    if len(payload) < 10:
        raise Mp3Error("truncated ID3v2 header")
    major, revision, flags = payload[3], payload[4], payload[5]
    if major not in (2, 3, 4):
        raise Mp3Error(f"unsupported ID3v2 version 2.{major}")
    size = _syncsafe(payload[6:10])
    end = 10 + size + (10 if flags & 0x10 else 0)
    if end > len(payload):
        raise Mp3Error("ID3v2 size exceeds payload")
    body = payload[10 : 10 + size]
    if flags & 0x80:
        body = body.replace(b"\xff\x00", b"\xff")
    frames, padding = _parse_id3_frames(body, major, max_frames=max_frames)
    id3v1 = _parse_id3v1(payload)
    return Id3Info(
        version=f"2.{major}.{revision}",
        revision=revision,
        flags=flags,
        size=size,
        frames=frames,
        id3v1=id3v1,
        padding=padding,
        end_offset=end,
    )


def scan_mpeg_frames(
    payload: bytes,
    *,
    start: int = 0,
    max_frames: int = 200_000,
) -> MpegScan:
    """Walk MPEG audio frames and report resynchronisation anomalies.

    The scanner is header-driven: it validates the sync word and every field
    needed to compute a frame length, then continues at the declared frame end.
    Bytes between frames are counted as sync losses, which is useful for hidden
    payloads and corrupted challenge files.
    """
    offset = max(0, start)
    frames: list[MpegFrame] = []
    invalid = 0
    sync_losses = 0
    truncated = 0
    inconsistent = 0
    first_offset = offset
    last_end = offset
    while offset + 4 <= len(payload) and len(frames) < max_frames:
        frame = _parse_frame(payload, offset)
        if frame is None:
            invalid += 1
            next_sync = payload.find(b"\xff", offset + 1)
            if next_sync < 0:
                break
            sync_losses += max(1, next_sync - offset)
            offset = next_sync
            continue
        if not frames:
            first_offset = offset
        elif (
            frame.version != frames[0].version
            or frame.layer != frames[0].layer
            or frame.sample_rate != frames[0].sample_rate
        ):
            inconsistent += 1
        if offset + frame.length > len(payload):
            truncated += 1
            break
        frames.append(frame)
        offset += frame.length
        last_end = offset
    trailing_offset = None
    trailing_size = 0
    if (
        frames
        and offset < len(payload)
        and not (len(payload) - offset >= 128 and payload[offset : offset + 3] == b"TAG")
    ):
        # A valid ID3v1 tag is not audio trailing data.
        trailing_offset = offset
        trailing_size = len(payload) - offset
    return MpegScan(
        frames=tuple(frames),
        first_offset=first_offset,
        last_end=last_end,
        invalid_headers=invalid,
        sync_losses=sync_losses,
        truncated_frames=truncated,
        inconsistent_frames=inconsistent,
        trailing_offset=trailing_offset,
        trailing_size=trailing_size,
    )


def _parse_frame(payload: bytes, offset: int) -> MpegFrame | None:
    if offset + 4 > len(payload):
        return None
    header = int.from_bytes(payload[offset : offset + 4], "big")
    if (header >> 21) & 0x7FF != 0x7FF:
        return None
    version = (header >> 19) & 0x3
    layer_bits = (header >> 17) & 0x3
    if version == 1 or layer_bits == 0:
        return None
    layer = 4 - layer_bits
    bitrate_index = (header >> 12) & 0xF
    sample_index = (header >> 10) & 0x3
    if bitrate_index in (0, 15) or sample_index == 3:
        return None
    version_key = 1 if version == 3 else 2
    bitrate = _BITRATES[(version_key, layer)][bitrate_index]
    sample_rate = _SAMPLE_RATES[version][sample_index]
    padding = (header >> 9) & 1
    channel_mode = (header >> 6) & 0x3
    if layer == 1:
        length = (12 * bitrate * 1000 // sample_rate + padding) * 4
    else:
        multiplier = 72 if version != 3 and layer == 3 else 144
        length = multiplier * bitrate * 1000 // sample_rate + padding
    if length < 4:
        return None
    return MpegFrame(
        offset=offset,
        length=length,
        version=version,
        layer=layer,
        bitrate=bitrate,
        sample_rate=sample_rate,
        padding=padding,
        channels=(
            "stereo",
            "joint stereo",
            "dual channel",
            "mono",
        )[channel_mode],
    )


def _parse_id3_frames(
    body: bytes,
    major: int,
    *,
    max_frames: int,
) -> tuple[tuple[Id3Frame, ...], int]:
    frames: list[Id3Frame] = []
    offset = 0
    padding = 0
    header_size = 6 if major == 2 else 10
    while offset + header_size <= len(body):
        if body[offset] == 0:
            padding = len(body) - offset
            break
        if major == 2:
            frame_id = body[offset : offset + 3].decode("latin-1", errors="replace")
            size = int.from_bytes(body[offset + 3 : offset + 6], "big")
            flags = 0
        else:
            frame_id = body[offset : offset + 4].decode("latin-1", errors="replace")
            raw_size = body[offset + 4 : offset + 8]
            size = _syncsafe(raw_size) if major == 4 else int.from_bytes(raw_size, "big")
            flags = int.from_bytes(body[offset + 8 : offset + 10], "big")
        if not frame_id.strip("\x00") or size <= 0 or offset + header_size + size > len(body):
            break
        payload = body[offset + header_size : offset + header_size + size]
        if not (flags & 0x00C0):  # compressed or encrypted frames are opaque here
            frames.append(_decode_frame(frame_id, payload, flags))
            if len(frames) >= max_frames:
                break
        offset += header_size + size
    return tuple(frames), padding


def _decode_frame(frame_id: str, payload: bytes, flags: int) -> Id3Frame:
    if not payload:
        return Id3Frame(frame_id, len(payload), 0, flags=flags)
    encoding = payload[0]
    text = payload[1:]
    if frame_id in {"TXXX", "TXX"}:
        description, value = _split_encoded(text, encoding)
        return Id3Frame(
            frame_id,
            len(payload),
            encoding,
            (value,) if value else (),
            description=description,
            flags=flags,
        )
    if frame_id in {"COMM", "COM", "USLT", "ULT"}:
        if len(text) < 3:
            return Id3Frame(frame_id, len(payload), encoding, flags=flags)
        language = text[:3].decode("latin-1", errors="replace")
        description, value = _split_encoded(text[3:], encoding)
        return Id3Frame(
            frame_id,
            len(payload),
            encoding,
            (value,) if value else (),
            description=f"{language}:{description}",
            flags=flags,
        )
    if frame_id in {"APIC", "PIC"}:
        if frame_id == "APIC" and b"\x00" in text:
            media = text.split(b"\x00", 1)[0].decode("latin-1", errors="replace")
        else:
            media = ""
        return Id3Frame(frame_id, len(payload), encoding, media_type=media, flags=flags)
    value = _decode_text(text, encoding)
    return Id3Frame(frame_id, len(payload), encoding, (value,) if value else (), flags=flags)


def _split_encoded(data: bytes, encoding: int) -> tuple[str, str]:
    if encoding in (1, 2):
        index = _find_utf16_terminator(data)
        if index < 0:
            return "", _decode_text(data, encoding)
        return _decode_text(data[:index], encoding), _decode_text(data[index + 2 :], encoding)
    index = data.find(b"\x00")
    if index < 0:
        return "", _decode_text(data, encoding)
    return _decode_text(data[:index], encoding), _decode_text(data[index + 1 :], encoding)


def _find_utf16_terminator(data: bytes) -> int:
    for index in range(0, len(data) - 1, 2):
        if data[index : index + 2] == b"\x00\x00":
            return index
    return -1


def _decode_text(data: bytes, encoding: int) -> str:
    codecs = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    codec = codecs.get(encoding, "latin-1")
    try:
        return data.rstrip(b"\x00").decode(codec, errors="replace").strip("\x00")
    except (UnicodeDecodeError, LookupError):
        return data.decode("latin-1", errors="replace").strip("\x00")


def _parse_id3v1(payload: bytes) -> dict[str, str]:
    if len(payload) < 128 or payload[-128:-125] != b"TAG":
        return {}
    tag = payload[-125:]

    def text(start: int, end: int) -> str:
        return tag[start:end].split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()

    genre = tag[124]
    return {
        "title": text(0, 30),
        "artist": text(30, 60),
        "album": text(60, 90),
        "year": text(90, 94),
        "comment": text(94, 124),
        "genre": _ID3V1_GENRES[genre] if genre < len(_ID3V1_GENRES) else str(genre),
    }


def _syncsafe(value: bytes) -> int:
    if len(value) != 4 or any(byte & 0x80 for byte in value):
        raise Mp3Error("invalid syncsafe integer")
    return (value[0] << 21) | (value[1] << 14) | (value[2] << 7) | value[3]


@register
class Id3Plugin(Analyzer):
    """Extract ID3v2 frames and ID3v1 text from MP3 files."""

    name: ClassVar[str] = "audio.id3"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("audio/mpeg", "audio/mp3", "audio/x-mpeg")
    priority: ClassVar[int] = 25
    description: ClassVar[str] = "Extract ID3v2 frames and ID3v1 text from MP3 audio."

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            info = parse_id3(artifact.payload)
        except Mp3Error as exc:
            ctx.skip(f"id3: {exc}")
        if info is None:
            ctx.skip("id3: no ID3 tag found")
        ctx.meta("id3/metadata", info.metadata(), source="id3")
        texts = info.texts()
        if texts:
            lines = []
            for frame_id, values in texts.items():
                for value in values:
                    if value:
                        lines.append(f"{frame_id}: {value}")
            if lines:
                ctx.text("id3/text", "\n".join(lines), frames=len(info.frames))


@register
class Mp3FramesPlugin(Analyzer):
    """Report MPEG frame counts, duration and resynchronisation anomalies."""

    name: ClassVar[str] = "audio.mp3_frames"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("audio/mpeg", "audio/mp3", "audio/x-mpeg")
    priority: ClassVar[int] = 26
    description: ClassVar[str] = "Walk MPEG audio frames and report header anomalies."
    options: ClassVar[dict[str, object]] = {"max_frames": 200_000}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        start = 0
        try:
            id3 = parse_id3(artifact.payload)
        except Mp3Error:
            id3 = None
        if id3 is not None:
            start = id3.end_offset
        scan = scan_mpeg_frames(
            artifact.payload,
            start=start,
            max_frames=max(1, int(ctx.option("max_frames", 200_000))),
        )
        ctx.meta("mp3/frames", scan.metadata(), source="mpeg")
        if scan.trailing_offset is not None and scan.trailing_size:
            trailing = artifact.payload[scan.trailing_offset :]
            ctx.blob(
                "mp3/trailing",
                trailing,
                media_type="application/octet-stream",
                offset=scan.trailing_offset,
                size=len(trailing),
            )
        if not scan.frames:
            ctx.skip("mp3 frames: no valid MPEG frame header found")
        if not scan.anomalous:
            ctx.skip("mp3 frames: no anomalies detected")


__all__ = [
    "Id3Frame",
    "Id3Info",
    "Id3Plugin",
    "Mp3Error",
    "Mp3FramesPlugin",
    "MpegFrame",
    "MpegScan",
    "parse_id3",
    "scan_mpeg_frames",
]
