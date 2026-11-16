# HTML reports and TUI

> **Status:** M4 complete. `nvox scan` writes `report.html` by default,
> `nvox report` re-renders a saved run without rescanning, and `nvox tui` opens
> the interactive Textual interface.

## Self-contained HTML report

Every scan writes `out/report.html` next to `findings.json`. Disable it with
`--no-html`, or re-render an existing report without running any plugins:

```bash
nvox scan challenge.png --flag-format 'ctf{}' --out out/
nvox report out/                         # writes out/report.html
nvox report out/findings.json --out /tmp/triage.html
nvox report out/ --open                  # open in the default browser
```

The HTML file embeds its CSS, JavaScript and preview data. It has no CDN,
font, stylesheet or image dependencies, so it opens from an air-gapped machine
or after being attached to a ticket.

### What the report contains

| Section | Contents |
|---|---|
| Summary | Files, artifacts, findings, skips, plugin runs, duration, formats and patterns |
| Findings | Filterable cards grouped by surface, with confidence, offset, context and per-finding provenance graph |
| Evidence | Input path, SHA-256, media type, size and artifact count |
| Artifact tree | Nested parent/child tree with producer, kind, size, depth, params and payload preview |
| Skips | Plugin, reason and affected artifact |
| Filters | Findings by text and confidence; artifacts by name, producer, kind or hash |

Text artifacts are shown inline (up to 4,000 characters) and image/audio
payloads are embedded as data URIs when they are at most 2 MiB. Larger payloads
show a size note instead, keeping the file manageable. Change the embedding
limit with `--max-preview-bytes` on `nvox report`; the scan command uses the
default.

All report strings are HTML-escaped. Artifact payloads are read from the store
next to `findings.json`; if the JSON is moved without the `artifacts/` directory,
metadata and findings still render and previews are marked unavailable.

## Textual TUI

Install the optional TUI extra once:

```bash
uv sync --extra tui
```

Open an existing report, or scan a new target:

```bash
nvox tui --report out/                 # browse a saved run
nvox tui challenge.png --out out/      # scan first, then browse
nvox tui                               # enter a path in the interface
```

The layout has an artifact tree on the left and tabs on the right:

- **Preview** — artifact metadata, text/hex payload preview, PNG dimensions.
- **Findings** — flag, confidence, surface, provenance chain and evidence.
- **Plugins** — checkboxes for every loaded plugin, plus all/none buttons, the
  evidence path, flag format and raw regex fields.

Keyboard shortcuts:

| Key | Action |
|---|---|
| `s` | Scan the path currently in the evidence field |
| `r` | Reload the report passed with `--report` |
| `q` | Quit |

The TUI uses the same engine, artifact store and graceful skip behavior as the
CLI. Missing external tools do not abort a TUI scan. A scan started from the
TUI writes its artifacts and `findings.json` to `--out` so it can be opened
again with `nvox report`.

## Terminal output

Interactive scans show a transient Rich progress line with the current file,
artifact, plugin, finding and skip counters. After the run, findings are grouped
by surface and provenance chain:

```text
Findings (2)
├── text (1)
│   └── evidence
│       └── ctf{direct} high offset 0x10 · evidence/note.txt
└── bitstream (1)
    └── image.lsb -> decoder.base64
        └── ctf{hidden} medium offset 0x0 · evidence/photo.png
```

Progress is disabled for `--json` and non-interactive output, so CI logs stay
clean and stdout remains valid JSON.
