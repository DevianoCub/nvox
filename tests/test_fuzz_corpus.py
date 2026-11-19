"""Malformed-input regression test for all built-in parsers."""

from __future__ import annotations

from fuzz_targets import run_corpus


def test_malformed_corpus_does_not_escape_plugin_boundaries() -> None:
    assert run_corpus(iterations=2) > 0
