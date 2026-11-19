"""Sliding-window Shannon entropy mapping."""

from __future__ import annotations

import math
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, ArtifactKind, Context, PluginKind, register
from nvox.plugins.image.png import encode_gray_png


def entropy(data: bytes) -> float:
    """Return the Shannon entropy of ``data`` in bits per byte."""
    if not data:
        return 0.0
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    total = len(data)
    result = 0.0
    for count in counts:
        if count:
            probability = count / total
            result -= probability * math.log2(probability)
    return result


def entropy_map(
    data: bytes,
    *,
    window: int = 256,
    step: int | None = None,
    max_windows: int = 4096,
) -> dict[str, Any]:
    """Calculate entropy for windows across ``data``.

    The window stride grows automatically when the default 256-byte stride
    would exceed ``max_windows``. The returned map still covers the payload
    from start to end within the requested window cap.
    """
    window = max(1, window)
    max_windows = max(1, max_windows)
    if step is None:
        step = max(window, max(1, math.ceil(len(data) / max_windows)))
    step = max(1, step)
    windows: list[dict[str, Any]] = []
    for offset in range(0, max(1, len(data)), step):
        chunk = data[offset : offset + window]
        if not chunk:
            break
        windows.append(
            {
                "offset": offset,
                "size": len(chunk),
                "entropy": round(entropy(chunk), 6),
            }
        )
        if len(windows) >= max_windows:
            break
    values = [entry["entropy"] for entry in windows]
    high = [entry for entry in windows if entry["entropy"] >= 7.0]
    return {
        "size": len(data),
        "window": window,
        "step": step,
        "windows": windows,
        "overall_entropy": round(entropy(data), 6),
        "min_entropy": min(values, default=0.0),
        "max_entropy": max(values, default=0.0),
        "mean_entropy": round(sum(values) / len(values), 6) if values else 0.0,
        "high_entropy_windows": len(high),
        "high_entropy_threshold": 7.0,
    }


@register
class EntropyPlugin(Analyzer):
    """Map entropy windows and optionally render the map as an image."""

    name: ClassVar[str] = "generic.entropy"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("*/*",)
    priority: ClassVar[int] = 88
    description: ClassVar[str] = "Map sliding-window Shannon entropy across an artifact."
    options: ClassVar[dict[str, object]] = {
        "window": 256,
        "step": 0,
        "max_windows": 4096,
        "emit_image": True,
    }

    def accepts(self, artifact: Artifact) -> bool:
        if artifact.kind in (ArtifactKind.TEXT, ArtifactKind.META):
            return False
        return super().accepts(artifact)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        step_raw = int(ctx.option("step", 0))
        mapping = entropy_map(
            artifact.payload,
            window=max(1, int(ctx.option("window", 256))),
            step=step_raw if step_raw > 0 else None,
            max_windows=max(1, int(ctx.option("max_windows", 4096))),
        )
        windows = mapping["windows"]
        if not windows:
            ctx.skip("entropy: no windows could be calculated")
        ctx.meta("entropy/map", mapping, source="sliding-window")
        ctx.text(
            "entropy/windows",
            "\n".join(
                f"{entry['offset']}\t{entry['size']}\t{entry['entropy']}" for entry in windows
            ),
            header="offset\tsize\tentropy",
        )
        if bool(ctx.option("emit_image", True)):
            values = bytes(
                max(0, min(255, round(float(entry["entropy"]) / 8.0 * 255.0))) for entry in windows
            )
            width = len(values)
            ctx.image(
                "entropy/heatmap.png",
                encode_gray_png(width, 1, values),
                media_type="image/png",
                source="entropy",
                width=width,
                height=1,
            )


__all__ = ["EntropyPlugin", "entropy", "entropy_map"]
