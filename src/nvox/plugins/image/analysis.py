"""Palette, alpha and cross-channel anomaly analysis."""

from __future__ import annotations

import math
from typing import ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register
from nvox.plugins.image.png import PngError, PngImage, decode_png


def _channel_stats(values: bytes) -> dict[str, object]:
    counts = [0] * 256
    for value in values:
        counts[value] += 1
    total = len(values)
    entropy = 0.0
    if total:
        for count in counts:
            if count:
                probability = count / total
                entropy -= probability * math.log2(probability)
    unique = sum(1 for count in counts if count)
    return {
        "min": min((value for value, count in enumerate(counts) if count), default=0),
        "max": max((value for value, count in enumerate(counts) if count), default=0),
        "unique": unique,
        "entropy": round(entropy, 6),
        "zero_count": counts[0],
        "full_count": counts[255],
    }


def analyze_png_channels(image: PngImage) -> dict[str, object]:
    """Build channel and palette statistics for a decoded PNG."""
    width = image.width
    height = image.height
    channels = image.channels
    pixels = image.pixels
    summary: dict[str, object] = {
        "width": width,
        "height": height,
        "channels": channels,
        "channel_stats": [_channel_stats(pixels[channel::channels]) for channel in range(channels)],
    }
    if channels in (2, 4):
        alpha = pixels[channels - 1 :: channels]
        transparent = sum(1 for value in alpha if value == 0)
        opaque = sum(1 for value in alpha if value == 255)
        semi = len(alpha) - transparent - opaque
        summary["alpha"] = {
            "transparent": transparent,
            "opaque": opaque,
            "semi_transparent": semi,
            "anomaly": semi > 0,
        }
    palette = image.palette
    if isinstance(palette, bytes):
        entries = [palette[index : index + 3] for index in range(0, len(palette), 3)]
        unique = len(set(entries))
        used = {value for value in pixels}
        summary["palette"] = {
            "entries": len(entries),
            "unique_entries": unique,
            "duplicate_entries": len(entries) - unique,
            "used_indices": len(used),
            "unused_entries": max(0, len(entries) - len(used)),
            "transparency": len(image.transparency or b""),
        }
    return summary


@register
class ChannelAnalysisPlugin(Analyzer):
    """Report channel statistics and emit XOR channel filters."""

    name: ClassVar[str] = "image.channels"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/png",)
    priority: ClassVar[int] = 60
    description: ClassVar[str] = "Analyse palette/alpha anomalies and XOR channel filters."
    options: ClassVar[dict[str, object]] = {
        "max_pixels": 16_000_000,
        "emit_filters": True,
        "max_filters": 3,
    }

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            image = decode_png(
                artifact.payload,
                max_pixels=max(1, int(ctx.option("max_pixels", 16_000_000))),
            )
        except PngError as exc:
            ctx.skip(f"channels: {exc}")
        summary = analyze_png_channels(image)
        ctx.meta("channels/stats", summary, source="png")
        if not bool(ctx.option("emit_filters", True)):
            return
        max_filters = max(0, int(ctx.option("max_filters", 3)))
        if max_filters == 0 or image.channels < 2:
            return
        pairs = [(0, 1), (1, 2), (0, 2)] if image.channels >= 3 else [(0, 1)]
        for left, right in pairs[:max_filters]:
            filtered = bytes(
                image.pixels[index] ^ image.pixels[index + (right - left)]
                for index in range(left, len(image.pixels), image.channels)
            )
            ctx.blob(
                f"channels/xor/{left}-{right}",
                filtered,
                width=image.width,
                height=image.height,
                left=left,
                right=right,
                filter="xor",
            )
        if image.channels in (2, 4):
            alpha = image.pixels[image.channels - 1 :: image.channels]
            ctx.blob(
                "channels/alpha",
                alpha,
                width=image.width,
                height=image.height,
                channel=image.channels - 1,
                filter="alpha",
            )


__all__ = ["ChannelAnalysisPlugin", "analyze_png_channels"]
