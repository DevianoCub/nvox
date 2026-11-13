# Flag engine

> **Status:** planned for M2. This page defines the behaviour to implement and
> the semantics users can rely on.

The flag engine takes user-defined formats and searches every surface Nvox can
produce. It is the reason the artifact graph exists: a flag only visible after
`LSB -> zlib -> base64` is still a normal match.

## Format syntax

A format is a literal prefix/suffix pattern where `{}` is the **only**
placeholder and stands for the flag body:

| Format | Matches |
|---|---|
| `ctf{}` | `ctf{...}` |
| `htb{}` | `htb{...}` |
| `pico{}` | `pico{...}` |
| `flag{}` | `flag{...}` |

Everything outside `{}` is escaped literally, so `HTB{}` does not match
`htb{...}` unless `-i` is given. The body is non-empty and does not cross a
newline; the default maximum body length is 200 characters.

Formats are repeatable and can be combined:

```bash
nvox scan evidence/ --flag-format 'ctf{}' --flag-format 'htb{}' --flag-format 'pico{}'
```

For formats that do not fit the placeholder model, pass a raw regular
expression with `--flag`:

```bash
nvox scan dump.bin --flag 'FLAG\{[A-Za-z0-9_]{8,64}\}' -i
```

Multiple formats are compiled into a single alternation with named groups, so a
scan does not cost more per added format.

## Surfaces

| Surface | What is scanned |
|---|---|
| Raw bytes | Evidence and artifacts, sliding window with overlap |
| Strings | ASCII and UTF-16LE/BE runs, including `strings`-style minimum length |
| Metadata | EXIF, XMP, IPTC, ICC, PNG chunks, JPEG segments, audio tags |
| Text artifacts | Every `text` artifact produced by analyzers |
| Bitstreams | LSB/LSB2/... planes, both bit orders, row and column major |
| Decoded layers | Output of the decoder chain, recursively |

Raw scanning uses a window with overlap equal to the longest possible match, so
a flag split across a chunk boundary is not missed. Surfaces are deduplicated,
so scanning a text artifact that came from raw bytes does not double-report.

## Decoder chain

Decoders are plugins. Built-ins (M2):

`base64`, `base64url`, `base32`, `base85`, `hex`, `url`, `rot13`, `rot47`,
`reverse`, `gzip`, `zlib`, `bzip2`, `xz`, single-byte `xor` (brute force).

The engine applies decoders breadth-first with:

- a depth budget (default 3 decoder layers, bounded by `scan.max_depth`);
- a visited set keyed by SHA-256, so cycles terminate;
- heuristic gating: base64/hex are only attempted on inputs that plausibly
  decode, and decompression only when the magic bytes match;
- a per-input and global time budget.

Every attempt is recorded, including unsuccessful decodes when `--verbose` is
set, because "what was ruled out" matters in a report.

## XOR brute force

Classic CTF payloads are often obfuscated with a single-byte XOR. For inputs up
to a configurable size, Nvox tries all 255 non-zero keys, scores the result and
runs the flag patterns on promising candidates. Scoring favours printable
ratio, presence of English/CTF keywords and a successful flag match. Disable
with `--no-xor-brute`.

## Findings

A finding is not just a string:

```json
{
  "value": "ctf{lsb_1s_fun}",
  "format": "ctf{}",
  "surface": "bitstream",
  "offset": 8128,
  "context": "...leading bytes [ctf{lsb_1s_fun}] trailing...",
  "artifact": "sha256:5f0c...",
  "chain": [
    {"plugin": "image.lsb_planes", "params": {"channel": 2, "bits": 1}},
    {"plugin": "decoder.zlib"},
    {"plugin": "decoder.base64"}
  ],
  "confidence": "high"
}
```

- **offset** is relative to the artifact where the match was found, not the
  original evidence; the chain explains how to get there.
- **chain** is the provenance path. `nvox extract` can reproduce any step.
- **confidence** reflects the surface and the number of independent matches:
  exact metadata or text matches are higher confidence than a single XOR hit.

## False positives

Generic-looking patterns such as `flag{}` can match documentation or test
fixtures. Mitigations:

- require a minimum body length (default 4, configurable);
- prefer structured surfaces over raw byte noise when ranking;
- allow `on_finding` hooks to drop matches;
- never hide lower-confidence findings, but sort them last and mark them.

## CLI summary

```bash
nvox scan photo.png --flag-format 'ctf{}'
nvox scan ./dump --flag 'flag\{[^}]+\}' -i --min-length 8
nvox scan image.png --flag-format 'htb{}' --no-xor-brute --out out/
```
