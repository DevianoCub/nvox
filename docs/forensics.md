# Forensics and evidence handling

> **Status:** principles are fixed now; hash manifests, chain logs and the
> output layout land with M1/M2. `docs/ru/` is reserved for the Russian
> translation and is empty for now.

Nvox is a triage and CTF tool, not a certified forensic suite. It is designed so
that its output can be explained and reproduced, which is what matters both for
a write-up and for a first-pass investigation.

## Principles

1. **Evidence is immutable.** Input files are opened read-only. Nvox never
   edits, renames or moves them, and never writes next to them.
2. **Everything is hashed.** Each input gets SHA-256, size, detected MIME type
   and ingestion timestamp before any analysis runs.
3. **Results are reproducible.** The report records Nvox version, Python
   version and the version of every external tool that contributed.
4. **Provenance is mandatory.** Every finding carries the chain of plugins and
   parameters that produced it.
5. **No hidden network access.** Analysis is local. Bridges that would need the
   network are out of scope; plugins must not use it by default.
6. **Bounded and explainable skips.** Depth, time and size limits are enforced,
   and anything skipped is listed with a reason.

## Planned output layout

```
out/
  manifest.json        # evidence hashes, run metadata, tool versions
  findings.json        # ranked findings with provenance chains
  artifacts/
    <sha256>           # stored artifacts (bitstreams, carved files, images)
  index.jsonl          # append-only artifact/finding event log
  report.html          # self-contained report (M4)
  run.log              # engine log with timings and skips
```

`findings.json` is the source of truth; HTML and console output are derived from
it. `nvox report` can re-render a run without rescanning.

## Manifest contents

```json
{
  "nvox": "0.1.0.dev0",
  "started_at": "2026-11-13T21:00:00Z",
  "finished_at": "2026-11-13T21:00:42Z",
  "evidence": [
    {"path": "challenge.png", "sha256": "…", "size": 913402, "mime": "image/png"}
  ],
  "config": {"flag_formats": ["ctf{}"], "max_depth": 4, "timeout": 120},
  "tools": [{"name": "exiftool", "version": "13.10", "path": "…"}]
}
```

The evidence hash is also repeated next to every finding, so a finding can be
tied back to exactly one input.

## Safety limits

| Risk | Mitigation |
|---|---|
| Zip bombs / decompression bombs | max artifact size, ratio check, depth limit |
| Infinite plugin chains | content-hash visited set, `max_depth` |
| Hanging external tools | per-plugin timeout, process-group kill |
| Huge output floods | stdout/stderr caps in the sandbox |
| Accidental modification | read-only opens, temp workdirs, no in-place writes |
| Hostile parsers | subprocess isolation where practical; Python parsers kept small |

## Reporting guidance

When publishing results, include:

- the manifest (hashes and versions);
- the provenance chain for each finding;
- an explicit note about which steps were skipped and why;
- the exact command line used.

This is enough for another person to reproduce the result without receiving the
original evidence.

## Scope and ethics

Use Nvox on files you own or are authorised to inspect. Analysing someone
else's private files, or using the stego tooling to exfiltrate data, is out of
scope and is not supported by the project.
