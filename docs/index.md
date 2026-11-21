# Nvox

Nvox is a modular forensic and steganography analysis framework for images,
audio and arbitrary files. Give it a file and an optional flag format, and it
walks an artifact graph — LSB planes, metadata, embedded files, decoded payloads
— looking for matches or collecting artifacts for later triage, while keeping a
provenance trail for everything it finds.

```bash
nvox scan photo.png --flag-format 'ctf{}'
```

## Design principles

1. **Artifact graph, not a checklist.** Every result is an artifact that can be
   analysed again. A flag hidden in `base64(zlib(LSB(pixels)))` is found by the
   same pipeline that analyses the original image.
2. **Everything is a plugin.** Analysers, extractors and decoders are plugins.
   External binaries are wrapped by declarative plugins. Core behaviour is
   reachable through hooks.
3. **Built-in first, external tools optional.** Nvox works on a clean Python
   install. When `exiftool`, `steghide`, `binwalk`, `zsteg` and friends are
   present, they add coverage automatically.
4. **Evidence is immutable.** Input files are opened read-only; all output goes
   to a separate directory and every artifact is addressed by SHA-256.
5. **Bounded analysis.** Depth, plugin timeouts, artifact size, archive
   expansion ratios and worker count are configurable and enforced. CLI scans
   write findings and provenance as JSON. Command plugins and external bridges
   run in bounded subprocesses; `scan.isolation = "process"` adds hard-timeout
   process isolation for eligible Python plugins. Python isolation is not an OS
   sandbox; see [Hardening](hardening.md).

## Current status

**0.1.0** is the first tagged release. All milestones through M7 are complete.

| Milestone | Scope | Status |
|---|---|---|
| M0 | Bootstrap, configuration, `nvox doctor` | done |
| M1 | Core engine: artifacts, registry, scheduler, hooks | done |
| M2 | Flag engine: formats, decoders, XOR brute, provenance | done |
| M3 | Deep image analysis and steganography | done |
| M4 | CLI polish, HTML report, TUI | done |
| M5 | External tool bridges, declarative plugin hardening | done |
| M6 | Audio and generic file analysis | done |
| M7 | Hardening, docs, 0.1 release | done |

Working commands today: `nvox scan`, `nvox doctor`,
`nvox plugins list|info|dirs`, `nvox extract`, `nvox report` and `nvox tui`.
M3 adds deep PNG/JPEG/GIF/BMP/TIFF metadata, configurable PNG bit planes,
palette/alpha/channel analysis, trailing/prepended and polyglot detection,
embedded-container carving and raw DEFLATE decoding. `nvox scan` also runs
without a flag format: it writes the complete artifact report to JSON/CSV and
HTML instead of printing everything in the terminal. M4 adds live progress,
grouped findings, a self-contained HTML report and a Textual triage interface.
M5 adds optional bridges for ExifTool, pngcheck, zsteg, steghide, stegseek,
outguess, StegSolve, binwalk, foremost, zbar and tesseract, plus a hardened
command-plugin sandbox. M6 adds the audio pack (WAV/MP3 metadata, sample LSB,
spectrograms, DTMF/Morse, echo hints) and generic analysis (entropy maps,
recursive archives, PDF and Office), with optional ffprobe and SoX bridges.
M7 adds archive ratio guards, deterministic malformed-input fuzzing, optional
process isolation for Python plugins, plugin templates and the signed 0.1.0
release pipeline. Post-0.1 work adds `generic.binary_tokens` for mixed `.bin`
payloads and `scripts/check-corpus.py` for real-case CTF/forensic regression
runs. Missing tools are recorded as skips and never abort a scan.

## Documentation

- [Getting started](getting-started.md)
- [Architecture](architecture.md)
- [CLI reference](cli.md)
- [Plugins](plugins.md)
- [Image analysis](image-analysis.md)
- [Audio analysis](audio-analysis.md)
- [Generic file analysis](generic-analysis.md)
- [HTML reports and TUI](report.md)
- [Flag engine](flag-engine.md)
- [Tooling setup](tooling.md)
- [Forensics and evidence handling](forensics.md)
- [Hardening and malformed input](hardening.md)
- [Performance and accelerator decision](performance.md)
- [Migrating to 0.1.0](migration.md)
- [Release checklist](release.md)
- [Development](development.md)
- [Roadmap](roadmap.md)

The Russian translation is in [`ru/`](ru/index.md). English pages are the
source of truth when the two differ.

## Legal

Use Nvox only on files you own or are authorised to examine: CTF challenges,
incident-response evidence, malware samples in a controlled lab, or your own
research data.
