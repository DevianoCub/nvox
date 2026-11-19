# Forensics and evidence handling

> **Status:** evidence ingestion, hashing and the content-addressed artifact
> store are implemented in M1. Findings and run metadata are written to
> `findings.json` in M2. A separate manifest with Python/tool versions and the
> self-contained HTML report land in M4. `docs/ru/` is reserved for the Russian
> translation and is empty for now.

Nvox is a triage and CTF tool, not a certified forensic suite. It is designed so
that its output can be explained and reproduced, which is what matters both for
a write-up and for a first-pass investigation.

## Principles

1. **Evidence is immutable.** Input files are opened read-only. Nvox never
   edits, renames or moves them, and never writes next to them.
2. **Everything is hashed.** Each input gets SHA-256, size and detected MIME
   type before any analysis runs. The payload is captured once, so an evidence
   file changing on disk mid-scan cannot affect the result.
3. **Results are reproducible.** `findings.json` records the Nvox version,
   formats, options, evidence hashes, skips and provenance chains. `nvox doctor
   --verbose` reports detected external tool versions; bridges record their
   command line and return code in artifact/skip params where applicable.
4. **Provenance is mandatory.** Every finding carries the chain of plugins and
   parameters that produced it.
5. **No hidden network access.** Analysis is local. Bridges that would need the
   network are out of scope; plugins must not use it by default.
6. **Bounded and explainable skips.** Depth, time and size limits are enforced,
   and anything skipped is listed with a reason.

## Output layout (M2)

```
out/
  artifacts/
    <xx>/<sha256>      # evidence, decoded payloads, bitstreams, metadata JSON
  index.jsonl          # append-only artifact metadata log
  findings.json        # run metadata, evidence records, findings and skips
```

`findings.json` is the source of truth for a run:

- `formats`, `patterns` and `options` describe exactly what was searched;
- each evidence entry contains path, SHA-256, size, MIME type, artifacts and
  skips;
- each finding links to its evidence and artifact hash and carries the
  first-parent provenance chain;
- `summary` counts files, artifacts, findings, skips and plugin runs.

An HTML report (`report.html`) and `nvox report` re-rendering without a rescan
are M4 features. A combined manifest separating tool versions from findings is
also M4; until then the report's top-level run metadata is the closest
equivalent.

## Run metadata

```json
{
  "nvox": "0.1.0",
  "started_at": "2026-11-14T12:00:00Z",
  "finished_at": "2026-11-14T12:00:06Z",
  "formats": ["ctf{}"],
  "patterns": [],
  "options": {"ignore_case": false, "min_length": 4, "max_length": 200},
  "evidence": [
    {
      "path": "challenge.png",
      "evidence": {
        "path": "challenge.png",
        "sha256": "…",
        "size": 913402,
        "media_type": "image/png"
      },
      "artifacts": ["…"],
      "skips": ["…"],
      "findings": ["…"],
      "plugin_runs": 42
    }
  ],
  "findings": ["…"],
  "summary": {"files": 1, "artifacts": 42, "findings": 1, "skips": 7}
}
```

The evidence hash is repeated in every finding's `evidence` object, so a finding
can be tied back to exactly one input. The example is abridged: artifact, skip
and finding entries contain the full records described above.

## Safety limits

| Risk | M2 mitigation |
|---|---|
| Zip bombs / decompression bombs | archive entry, per-file, total and ratio caps; decoder output and artifact size caps; depth limit |
| Infinite plugin chains | content-hash dedup, per-plugin visited set, `max_depth` |
| Hanging plugins | cooperative per-plugin timeout; hard process-group kill for command plugins and bridges |
| Huge output floods | artifact size cap; streamed stdout/stderr and output-file caps for command plugins and bridges |
| Accidental modification | read-only evidence reads, separate output directory, no in-place writes |
| Hostile parsers | small stdlib parsers with explicit bounds; deterministic malformed-input corpus; optional process isolation for untrusted Python plugins |

The cooperative timeout abandons a worker thread; it cannot forcibly stop Python
code that ignores the deadline. Command plugins and external bridges run in a
separate process group with streamed output caps and best-effort POSIX rlimits,
but this is not a security boundary against a hostile local user. Do not treat
the default worker-thread model as a sandbox for untrusted Python plugins.
`scan.isolation = "process"` hard-kills an eligible Python plugin on timeout,
but it does not restrict filesystem or network access; use an OS sandbox for
hostile code. See [Hardening](hardening.md).

## Reporting guidance

When publishing results, include:

- the evidence SHA-256 values and the run metadata from `findings.json`;
- the provenance chain for each finding;
- an explicit note about which steps were skipped and why (`--verbose` lists
  them, and they are always present in the JSON);
- the exact command line used.

This is enough for another person to reproduce the result without receiving the
original evidence, provided they can obtain the same input.

## Scope and ethics

Use Nvox on files you own or are authorised to inspect. Analysing someone
else's private files, or using the stego tooling to exfiltrate data, is out of
scope and is not supported by the project.
