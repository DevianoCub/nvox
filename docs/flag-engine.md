# Flag engine

> **Status:** implemented through M3 for raw/text/metadata/bitstream surfaces
> and the built-in decoder chain. M3 adds XMP/IPTC/ICC, PNG/JPEG/GIF/BMP/TIFF
> structure and configurable PNG bit planes. HTML/TUI reporting remains M4.

The flag engine takes user-defined formats and searches every surface Nvox can
produce. It is the reason the artifact graph exists: a flag only visible after
`LSB -> zlib -> base64` is still a normal match.

## Format syntax

A format is a literal pattern with exactly one `{}` placeholder. The braces are
part of the flag syntax, so `ctf{}` matches `ctf{...}`:

| Format | Matches |
|---|---|
| `ctf{}` | `ctf{...}` |
| `htb{}` | `htb{...}` |
| `pico{}` | `pico{...}` |
| `flag{}` | `flag{...}` |

Everything outside `{}` is escaped literally. `HTB{}` does not match
`htb{...}` unless `-i` is given. The body is non-empty and does not cross a
newline. Minimum and maximum body lengths default to 4 and 200 characters and
are controlled by `--min-length` / `--max-length`.

Formats are repeatable:

```bash
nvox scan evidence/ --flag-format 'ctf{}' --flag-format 'htb{}' --flag-format 'pico{}'
```

For formats that do not fit the placeholder model, pass a raw regular
expression with `--flag`:

```bash
nvox scan dump.bin --flag 'FLAG\{[A-Za-z0-9_]{8,64}\}' -i
```

Raw patterns define their own bounds. M2 compiles them for byte scanning, so
Unicode-only constructs that cannot be encoded as bytes are rejected. Each
format and raw pattern is compiled separately, which keeps the original format
label in every finding. Overlapping matches are deduplicated by value.

## Surfaces

| Surface | Current implementation |
|---|---|
| Raw bytes | The complete in-memory artifact payload |
| Strings | ASCII and UTF-16LE/BE runs, with absolute byte offsets |
| Text artifacts | Every `text` artifact produced by analyzers or decoders |
| Metadata | JSON metadata artifacts plus EXIF, XMP, IPTC, ICC and chunk text |
| Bitstreams | PNG bit planes and channel/palette analysis artifacts |
| Decoded layers | Output of the decoder chain, recursively |

The scanner searches both raw bytes and text runs. A match found by both is
reported once, with the more structured surface. M3 metadata covers PNG chunks,
JPEG `APPn`/`COM`, GIF extensions, BMP/TIFF headers and strings, plus XMP/IPTC/
ICC text. Bitplane extraction supports bits 1-8, all channels, both bit orders
and row/column layouts.

## Decoder chain

Decoders are plugins under `src/nvox/plugins/decoders/`. Built-ins:

`base64`, `base64url`, `base32`, `base85`, `hex`, `url`, `rot13`, `rot47`,
`reverse`, `gzip`, `zlib`, `bzip2`, `xz`, `deflate` (raw and embedded zlib),
single-byte `xor` (brute force).

They run as ordinary plugins, so every decoded layer is stored, hashed and fed
back into the graph. The engine applies them breadth-first with:

- the normal artifact depth budget (`scan.max_depth`, default 4);
- per-plugin visited keys and content-addressed storage, so cycles terminate;
- heuristic gating: base encodings must validate and decode to meaningful text
  or a known magic; decompression requires the container magic;
- decompression output caps (`scan.max_artifact_bytes`);
- a per-invocation timeout and the global plugin-run budget.

Unsuccessful attempts are recorded as plugin skips with a reason. Use
`nvox scan --verbose` to inspect them.

## XOR brute force

For byte artifacts up to 16 KiB by default, Nvox tries all 255 non-zero
single-byte keys, scores the results and emits the best candidates as text
artifacts. The scanner then searches those artifacts normally. Scoring favours
printable text, letters/digits, brace pairs and CTF keywords. The defaults are
deliberately conservative to keep the artifact graph bounded:

```toml
[plugins.options."decoder.xor"]
max_input_bytes = 16384
max_candidates = 5
min_printable = 0.75
```

Disable the technique with `--no-xor-brute`.

## Findings

A finding is not just a string:

```json
{
  "value": "lsb_1s_fun",
  "format": "ctf{}",
  "surface": "bitstream",
  "offset": 128,
  "context": "…leading bytes ctf{lsb_1s_fun} trailing…",
  "artifact": "5f0c…",
  "confidence": "medium",
  "chain": [
    {
      "plugin": "image.lsb",
      "name": "lsb/channel=1",
      "sha256": "5f0c…",
      "params": {"bit": 1, "channel": 1}
    },
    {
      "plugin": "decoder.base64",
      "name": "base64",
      "sha256": "9a31…",
      "params": {"codec": "base64"}
    }
  ],
  "evidence": {"sha256": "…", "path": "photo.png"}
}
```

- **offset** is relative to the artifact where the match was found, not the
  original evidence. The chain explains how to get there.
- **chain** is the first-parent provenance path from evidence to artifact. The
  evidence root itself is not a transformation step, so a direct evidence match
  has an empty chain.
- **artifact** is the SHA-256 of the artifact that contains the match;
  `nvox extract <sha256>` writes it back to disk.
- **confidence** is surface-based: `high` for text/metadata, `medium` for
  strings, bitstreams and XOR candidates, `low` for a direct raw-byte match.
  When the same value is found through several chains, the shortest chain wins,
  then the higher confidence.

## False positives

Generic-looking patterns such as `flag{}` can match documentation or test
fixtures. Mitigations:

- require a minimum body length (default 4, configurable);
- prefer structured surfaces and shorter provenance chains when the same value
  is found more than once;
- allow `on_finding` hooks to modify or drop matches;
- keep every distinct value, but report one best occurrence per value and
  evidence file.

## CLI summary

```bash
nvox scan photo.png --flag-format 'ctf{}'
nvox scan ./dump --flag 'flag\{[^}]+\}' -i --min-length 8
nvox scan image.png --flag-format 'htb{}' --no-xor-brute --out out/
nvox scan image.png --flag-format 'pico{}' --json
```

Exit code `2` means at least one flag was found, so the command works directly
in CI. `findings.json` is written under `--out` even without `--json`.
