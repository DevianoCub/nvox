# Changelog

All notable changes are recorded here. The project follows semantic versioning
from 0.1.0.

## 0.1.0 — 2026-11-19

First tagged release.

### Added

- Built-in decoders, image/audio/generic analysis packs and optional external
  bridges.
- Flag engine with provenance chains, JSON/CSV output, HTML reports and a
  Textual TUI.
- Declarative TOML command plugins with a bounded sandbox.
- Recursive archive extraction with entry, size, total and compression-ratio
  limits.
- Optional `scan.isolation = "process"` runner for eligible Python plugins,
  with hard timeout termination; the cooperative thread runner remains the
  default.
- Deterministic malformed-input fuzz corpus and a longer fuzz script.
- Repeatable full-pipeline benchmark and the no-Rust/PyO3 decision record.
- Python and command plugin templates, conformance tests and migration notes.
- Russian documentation under `docs/ru/`.
- Linux/macOS CI, strict docs CI and a tag-triggered signed release workflow.

### Changed

- Version promoted from `0.1.0.dev0` to `0.1.0`.
- Highly compressible archive members are skipped by default instead of being
  expanded beyond the ratio budget.
- Documentation status, CLI reference and configuration examples cover the
  hardening and isolation controls.
