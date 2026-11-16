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
5. **Bounded analysis.** Depth, plugin timeouts, artifact size and worker count
   are configurable and enforced. CLI scans write findings and provenance as
   JSON; cooperative timeouts are not a security sandbox (M5/M7 add isolation).

## Current status

Nvox is under active development.

| Milestone | Scope | Status |
|---|---|---|
| M0 | Bootstrap, configuration, `nvox doctor` | done |
| M1 | Core engine: artifacts, registry, scheduler, hooks | done |
| M2 | Flag engine: formats, decoders, XOR brute, provenance | done |
| M3 | Deep image analysis and steganography | done |
| M4 | CLI polish, HTML report, TUI | next |
| M5 | External tool bridges, declarative plugin hardening | planned |
| M6 | Audio and generic file analysis | planned |
| M7 | Hardening, docs, 0.1 release | planned |

Working commands today: `nvox scan`, `nvox doctor`,
`nvox plugins list|info|dirs` and `nvox extract`. M3 adds deep PNG/JPEG/GIF/BMP/
TIFF metadata, configurable PNG bit planes, palette/alpha/channel analysis,
trailing/prepended and polyglot detection, embedded-container carving and raw
DEFLATE decoding. `nvox scan` also runs without a flag format: it writes the
complete artifact report to JSON/CSV instead of printing everything in the
terminal. Everything documented as "planned" describes the target design and is
not implemented yet; each page marks its status explicitly.

## Documentation

- [Getting started](getting-started.md)
- [Architecture](architecture.md)
- [CLI reference](cli.md)
- [Plugins](plugins.md)
- [Image analysis](image-analysis.md)
- [Flag engine](flag-engine.md)
- [Tooling setup](tooling.md)
- [Forensics and evidence handling](forensics.md)
- [Development](development.md)
- [Roadmap](roadmap.md)

Russian documentation is planned for `docs/ru/`; the directory exists but is
intentionally empty for now.

## Legal

Use Nvox only on files you own or are authorised to examine: CTF challenges,
incident-response evidence, malware samples in a controlled lab, or your own
research data.
