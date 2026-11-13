"""Nvox - modular forensic and steganography analysis framework."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nvox")
except PackageNotFoundError:  # pragma: no cover - running from a source tree only
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
