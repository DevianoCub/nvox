"""Stable plugin API surface.

Plugins target this module so that the import path stays fixed across
releases. The API version is bumped only for breaking changes.

Milestone M1 introduces the plugin base classes (:class:`Analyzer`,
:class:`Extractor`, :class:`Decoder`), the artifact model and the
registration decorator. Nothing is exported yet.
"""

from __future__ import annotations

API_VERSION = 1

__all__ = ["API_VERSION"]
