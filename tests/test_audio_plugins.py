"""Tests for the M6 audio pack: WAV, MP3, spectrograms and tone decoders."""

from __future__ import annotations

import json
import math
import random

import pytest

from fixture_builders import (
    make_dtmf_wav,
    make_morse_wav,
    make_mp3,
    make_wav,
    make_wav_lsb,
)
from nvox.api import ArtifactKind, Context, PluginSkip, make_artifact
from nvox.plugins.audio.mp3 import (
    Id3Plugin,
    Mp3Error,
    Mp3FramesPlugin,
    parse_id3,
    scan_mpeg_frames,
)
from nvox.plugins.audio.spectrogram import SpectrogramPlugin, fft, spectrogram
from nvox.plugins.audio.tones import (
    DtmfMorsePlugin,
    EchoHidingPlugin,
    autocorrelation_peaks,
    detect_dtmf,
    detect_morse,
)
from nvox.plugins.audio.wav import (
    WavError,
    WavLsbPlugin,
    WavMetadataPlugin,
    decode_pcm_samples,
    extract_sample_lsb,
    mono_samples,
    parse_wav,
)


def _run(
    plugin: object,
    payload: bytes,
    media_type: str,
    *,
    options: dict[str, object] | None = None,
) -> tuple[object, ...]:
    artifact = make_artifact(
        kind=ArtifactKind.AUDIO,
        name="audio",
        payload=payload,
        media_type=media_type,
    )
    ctx = Context(
        artifact,
        plugin.name,  # type: ignore[attr-defined]
        max_artifact_bytes=16 * 1024 * 1024,
        depth=1,
        options=options,
    )
    plugin.run(artifact, ctx)  # type: ignore[attr-defined]
    return ctx.artifacts


def _meta(artifacts: tuple[object, ...], name: str) -> dict[str, object]:
    artifact = next(item for item in artifacts if item.name == name)  # type: ignore[attr-defined]
    return json.loads(artifact.text)  # type: ignore[attr-defined]


def _mono(payload: bytes) -> tuple[list[float], int]:
    info = parse_wav(payload)
    values = decode_pcm_samples(payload, info)
    return mono_samples(values, info.channels), info.sample_rate


def test_wav_metadata_reports_chunks_duration_and_trailing() -> None:
    payload = make_wav([0, 1000, -1000, 500], sample_rate=8000, trailing=b"ctf{trailing}")
    info = parse_wav(payload)
    assert info.sample_rate == 8000
    assert info.channels == 1
    assert info.bits_per_sample == 16
    assert info.frame_count == 4
    assert info.duration == pytest.approx(0.0005)
    assert [chunk.id for chunk in info.chunks] == ["fmt ", "data"]
    assert info.trailing_offset is not None
    assert info.trailing_size == len(b"ctf{trailing}")

    artifacts = _run(WavMetadataPlugin(), payload, "audio/wav")
    metadata = _meta(artifacts, "wav/metadata")
    assert metadata["sample_rate"] == 8000
    assert metadata["frames"] == 4
    assert metadata["trailing_bytes"] == len(b"ctf{trailing}")
    trailing = next(item for item in artifacts if item.name == "wav/trailing")
    assert trailing.payload == b"ctf{trailing}"  # type: ignore[attr-defined]


def test_wav_lsb_extracts_text_and_respects_bit_option() -> None:
    hidden = "ctf{wav_lsb}"
    payload = make_wav_lsb(hidden)
    info = parse_wav(payload)
    assert extract_sample_lsb(payload, info).startswith(hidden.encode())

    artifacts = _run(WavLsbPlugin(), payload, "audio/wav")
    plane = next(item for item in artifacts if item.name.startswith("wav/lsb/"))
    assert plane.payload.startswith(hidden.encode())  # type: ignore[attr-defined]
    assert plane.params["bit"] == 1  # type: ignore[attr-defined]

    second = "ctf{second_plane}"
    payload = make_wav_lsb(second, bit=2)
    artifacts = _run(WavLsbPlugin(), payload, "audio/wav", options={"bit": 2})
    plane = next(item for item in artifacts if item.name.startswith("wav/lsb/"))
    assert plane.payload.startswith(second.encode())  # type: ignore[attr-defined]
    assert plane.params["bit"] == 2  # type: ignore[attr-defined]


def test_wav_helpers_reject_invalid_payloads() -> None:
    with pytest.raises(WavError):
        parse_wav(b"RIFF")
    info = parse_wav(make_wav_lsb("ctf{bits}"))
    with pytest.raises(WavError):
        extract_sample_lsb(b"", info, bit=99)


def test_mp3_id3_and_frame_anomalies() -> None:
    payload = make_mp3(
        title="ctf{id3_title}",
        artist="ctf{id3_artist}",
        album="ctf{id3_album}",
        frames=3,
        garbage=b"JUNK",
        trailing=b"ctf{mp3_trailing}",
    )
    info = parse_id3(payload)
    assert info is not None
    assert info.texts()["TIT2"] == ["ctf{id3_title}"]
    assert info.texts()["TPE1"] == ["ctf{id3_artist}"]

    artifacts = _run(Id3Plugin(), payload, "audio/mpeg")
    metadata = _meta(artifacts, "id3/metadata")
    assert metadata["version"] == "2.3.0"
    text = next(item for item in artifacts if item.name == "id3/text")
    assert b"ctf{id3_title}" in text.payload  # type: ignore[attr-defined]

    scan = scan_mpeg_frames(payload, start=info.end_offset)
    assert len(scan.frames) == 3
    assert scan.anomalous
    assert scan.sync_losses > 0
    assert scan.trailing_size == len(b"ctf{mp3_trailing}")

    artifacts = _run(Mp3FramesPlugin(), payload, "audio/mpeg")
    frames = _meta(artifacts, "mp3/frames")
    assert frames["frames"] == 3
    assert frames["trailing_bytes"] == len(b"ctf{mp3_trailing}")
    trailing = next(item for item in artifacts if item.name == "mp3/trailing")
    assert trailing.payload == b"ctf{mp3_trailing}"  # type: ignore[attr-defined]


def test_mp3_clean_frames_and_missing_tag_skip() -> None:
    payload = make_mp3(frames=2)
    info = parse_id3(payload)
    assert info is not None
    scan = scan_mpeg_frames(payload, start=info.end_offset)
    assert len(scan.frames) == 2
    assert not scan.anomalous

    plain = payload[info.end_offset :]
    assert parse_id3(plain) is None
    plugin = Id3Plugin()
    artifact = make_artifact(
        kind=ArtifactKind.AUDIO,
        name="plain.mp3",
        payload=plain,
        media_type="audio/mpeg",
    )
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip):
        plugin.run(artifact, ctx)


def test_mp3_rejects_truncated_id3() -> None:
    with pytest.raises(Mp3Error):
        parse_id3(b"ID3\x04\x00\x00\x7f\x7f\x7f\x7f")


def test_fft_and_spectrogram_render_a_png() -> None:
    assert fft([1, 0, 0, 0]) == [1, 1, 1, 1]
    with pytest.raises(ValueError):
        fft([1, 0, 0])

    sample_rate = 8000
    samples = [math.sin(2.0 * math.pi * 440.0 * index / sample_rate) * 0.5 for index in range(4096)]
    width, height, pixels, metadata = spectrogram(samples, sample_rate)
    assert 1 <= width <= 2000
    assert height == 512
    assert len(pixels) == width * height
    assert metadata["sample_rate"] == sample_rate

    payload = make_wav([int(value * 30000) for value in samples], sample_rate=sample_rate)
    artifacts = _run(SpectrogramPlugin(), payload, "audio/wav")
    image = next(item for item in artifacts if item.name == "spectrogram/spectrogram.png")
    assert image.payload.startswith(b"\x89PNG\r\n\x1a\n")  # type: ignore[attr-defined]
    assert image.params["width"] == width  # type: ignore[attr-defined]
    assert _meta(artifacts, "spectrogram/meta")["height"] == height


def test_dtmf_detection_and_plugin_output() -> None:
    for sequence in ("123A", "*0#D"):
        payload = make_dtmf_wav(sequence)
        samples, sample_rate = _mono(payload)
        hits = detect_dtmf(samples, sample_rate)
        assert "".join(hit.value for hit in hits) == sequence

        artifacts = _run(DtmfMorsePlugin(), payload, "audio/wav")
        text = next(item for item in artifacts if item.name == "tones/dtmf")
        assert text.text == sequence  # type: ignore[attr-defined]


def test_morse_detection_and_plugin_output() -> None:
    for message in ("SOS", "HI 42"):
        payload = make_morse_wav(message)
        samples, sample_rate = _mono(payload)
        text, events = detect_morse(samples, sample_rate)
        assert text == message
        assert events

        artifacts = _run(DtmfMorsePlugin(), payload, "audio/wav")
        morse = next(item for item in artifacts if item.name == "tones/morse")
        assert morse.text == message  # type: ignore[attr-defined]


def test_tones_skip_silent_audio() -> None:
    payload = make_wav([0] * 4096)
    plugin = DtmfMorsePlugin()
    artifact = make_artifact(
        kind=ArtifactKind.AUDIO,
        name="silence.wav",
        payload=payload,
        media_type="audio/wav",
    )
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip):
        plugin.run(artifact, ctx)


def test_echo_hiding_autocorrelation_peaks_and_plugin() -> None:
    generator = random.Random(7)
    length = 4000
    lag = 100
    base = [generator.uniform(-1.0, 1.0) for _ in range(length)]
    signal = list(base)
    for index in range(length - lag):
        signal[index + lag] += 0.8 * base[index]
    samples = [int(value * 12000) for value in signal]

    peaks = autocorrelation_peaks(samples, min_lag=20, max_lag=200)
    assert peaks
    assert peaks[0]["lag_samples"] == lag

    payload = make_wav(samples, sample_rate=8000)
    artifacts = _run(
        EchoHidingPlugin(),
        payload,
        "audio/wav",
        options={"min_lag": 20, "max_lag": 200, "threshold": 0.25},
    )
    metadata = _meta(artifacts, "echo/peaks")
    assert metadata["peaks"]
    assert metadata["peaks"][0]["lag_samples"] == lag  # type: ignore[index]
