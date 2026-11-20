# Generic file analysis

> **Status:** M6 complete, plus post-0.1 binary token extraction. The generic
> pack adds entropy mapping, recursive archive extraction, PDF structure/text
> checks, OOXML (DOCX/XLSX/PPTX) inspection, UTF-32 strings and individual
> binary-token hand-off to decoders. ZIP/TAR and gzip/bzip2/xz recursion use
> bounded standard-library extractors; archive contents return to the artifact
> graph like any other file.

Generic analysis is the fallback pack for inputs that are not images or audio.
Every extractor emits child artifacts with provenance, so a flag inside a ZIP
inside a TAR inside a gzip stream is found by the same engine that scans a plain
text file.

## Techniques

| Area | Built-in coverage |
|---|---|
| Strings | ASCII, UTF-16LE/BE and UTF-32LE/BE printable runs with byte offsets, minimum length and deduplication |
| Binary tokens | Individual base64/base64url/hex/URL-escaped/braced tokens from binary strings, including UTF-16, emitted separately so the decoder chain can process mixed `.bin` payloads |
| Entropy | Shannon entropy for the whole payload and sliding windows, step control, high-entropy window count, text table and grayscale heatmap |
| ZIP | Central-directory entries, deflate/store, per-entry metadata, CRC, unsafe path rejection, encryption detection, file/total/ratio budgets |
| TAR | Uncompressed and compressed TAR members, regular-file extraction, per-file and total budgets |
| Single-stream compression | gzip, bzip2 and xz decompression with a hard output cap; TAR payloads are re-emitted as an archive for another pass |
| 7z/RAR | Detected and reported, but extraction requires an external tool |
| PDF | Header version, object/page counts, metadata strings, Flate/ASCIIHex/ASCII85 streams, text extraction, JavaScript/OpenAction/Launch/EmbeddedFile flags, `%%EOF` count and trailing bytes |
| Office | DOCX/XLSX/PPTX detection from OOXML parts, core/app metadata, macro presence, media/embedded/hidden parts and external relationships |

Archive recursion is depth-limited by `scan.max_depth`, and plugin output is
limited by `scan.max_artifact_bytes` in addition to the plugin's own budgets.

## Plugins and options

| Plugin | Purpose | Main options |
|---|---|---|
| `generic.strings` | ASCII/UTF-16/UTF-32 printable runs | `min_length=4`, `utf16=True`, `utf32=True` |
| `generic.binary_tokens` | Individual encoded tokens for the decoder chain | `max_input_bytes=8000000`, `min_token_length=8`, `max_token_bytes=4096`, `max_tokens=64`, `utf16=True` |
| `generic.entropy` | Entropy map and heatmap PNG | `window=256`, `step=0` (auto), `max_windows=4096`, `emit_image=True` |
| `generic.archive` | Recursive ZIP/TAR/gzip/bzip2/xz extraction | `max_entries=256`, `max_file_bytes=33554432`, `max_total_bytes=100663296`, `max_ratio=1000` |
| `generic.pdf` | PDF metadata, streams, text and actions | `max_stream_bytes=8388608` |
| `generic.office` | OOXML metadata, macros and links | `max_part_bytes=4194304` |

Configure tighter archive budgets for untrusted inputs:

```toml
[plugins.options."generic.archive"]
max_entries = 128
max_file_bytes = 8388608
max_total_bytes = 33554432
max_ratio = 200

[plugins.options."generic.pdf"]
max_stream_bytes = 4194304

[plugins.options."generic.entropy"]
window = 1024
max_windows = 2048
```

## Examples

Scan an archive recursively without external tools:

```bash
nvox scan bundle.zip --flag-format 'ctf{}' --no-external --out out/
```

Scan a PDF and keep the extracted streams, metadata and trailing data:

```bash
nvox scan challenge.pdf --flag-format 'htb{}' --out out/
```

Scan a mixed binary payload where one base64 token is surrounded by noise:

```bash
nvox scan challenge.bin --flag-format 'ctf{}' --out out/
```

`generic.binary_tokens` emits each plausible token as its own text artifact, so
`decoder.base64`, `decoder.hex` and the other decoders can process it
individually. Adjust the budget for large binaries:

```toml
[plugins.options."generic.binary_tokens"]
max_input_bytes = 2000000
max_tokens = 32
utf16 = false
```

Inspect entropy around an appended payload:

```toml
[plugins.options."generic.entropy"]
window = 512
step = 256
emit_image = true
```

```bash
nvox scan suspicious.bin --out out/
```

The entropy map is written to `entropy/map` (JSON), `entropy/windows` (TSV) and
`entropy/heatmap.png`. Text and metadata artifacts are included in the HTML
report and searched by the normal flag engine.

## Fixtures and coverage

All fixtures are built with the standard library. Nested archives, PDF streams
and OOXML packages are assembled in `tests/fixture_builders.py`.

| Technique | Fixture / test |
|---|---|
| Entropy values, windows and heatmap | `test_entropy_helpers_and_plugin_map` |
| ZIP extraction and provenance | `make_zip_entries`, `test_zip_entries_are_extracted_with_provenance` |
| Unsafe paths and size budgets | `test_zip_guards_reject_unsafe_paths_and_oversized_files` |
| TAR, gzip and gzip-compressed TAR | `make_tar`, `make_gzip`, `test_tar_and_compressed_streams_are_extracted` |
| Broken ZIP and unsupported 7z | `test_archive_errors_become_skips` |
| Recursive ZIP chain | `test_recursive_archive_chain_finds_flag` |
| PDF metadata, compressed text, actions and trailing data | `make_pdf`, `test_pdf_metadata_text_actions_and_trailing` |
| Invalid/empty PDF | `test_pdf_rejects_non_pdf_and_skips_empty_documents` |
| DOCX metadata, macros and external links | `make_docx`, `test_docx_metadata_macros_and_external_links` |
| Plain ZIP and malformed OOXML | `test_office_ignores_plain_zip_and_rejects_bad_packages` |
| UTF-32LE/BE strings | `test_strings_plugin_extracts_utf32_variants`, `test_utf32le_runs_report_byte_offsets` |
| Binary token extraction and decoder chain | `tests/test_binary_tokens.py` |
| Real-case corpus runner | `tests/test_check_corpus.py` |

Run only the M6 generic tests:

```bash
.venv/bin/pytest -o addopts='' -q tests/test_generic_plugins.py tests/test_surfaces.py
```

## Limitations

- 7z and RAR are recognised but not extracted by the built-in pack. Their media
  types produce a skip that points at the optional external-tool path.
- Archive extraction is in-memory and bounded. Very large archives may be
  partially skipped; the `archive/summary` artifact records the reason for every
  skipped member.
- The PDF parser is a structural scanner, not a full renderer. It does not follow
  every xref table, object stream or font encoding, and encrypted PDFs are
  reported rather than decrypted.
- Office support covers OOXML packages (DOCX/XLSX/PPTX). Legacy binary `.doc`,
  `.xls` and `.ppt` files are treated as generic bytes.
- Entropy windows use a fixed byte stride. A short hidden payload may not align
  with a window boundary; lower `window` and `step` to inspect it more closely.
- `generic.archive` is an in-process Python extractor. It applies entry,
  per-file, total-size and decompression-ratio guards (checked before and after
  reading) but is not a security sandbox. See [Hardening](hardening.md) for
  limits, process isolation and the malformed-input fuzz corpus.
- `generic.binary_tokens` is a token splitter, not a format detector. It does
  not decode payloads itself and does not decide whether a token is a flag; it
  only gives the existing decoders a clean candidate. Custom or encrypted
  encodings still need a dedicated plugin or an external tool.
