# Image analysis

> **Status:** M3 complete, with M5 optional bridges. The built-in image pack
> parses PNG, JPEG, GIF, BMP and TIFF metadata and structure, extracts
> configurable PNG bit planes, detects channel/palette anomalies, carves embedded
> containers, and decodes raw deflate streams. QR/barcode and OCR are available
> through the optional `bridge.zbar` and `bridge.tesseract` plugins.

Nvox treats every image technique as a normal plugin. Extracted chunks, text,
bit planes and carved files become artifacts, so they are hashed, searched for
flags, and can be decoded again like any other input.

## Techniques

| Area | Built-in coverage |
|---|---|
| PNG chunks | `IHDR`, `PLTE`, `IDAT`, `IEND`, `tEXt`, `zTXt`, `iTXt`, `eXIf`, `iCCP`, `gAMA`, `pHYs`, `tIME`, `sRGB`, `cHRM`, `tRNS`; CRC validation; unknown-chunk capture |
| PNG pixels | Non-interlaced 1/2/4/8-bit grayscale, RGB, palette, grayscale+alpha and RGBA; unfiltering; palette and transparency data |
| PNG bit planes | Bits 1-8, any channel or all channels, MSB/LSB bit order, row-major or column-major packing |
| PNG anomalies | Per-channel entropy/min/max/zero/full counts, alpha distribution, duplicate and unused palette entries, XOR channel filters, optional bit-plane preview PNGs |
| JPEG | `APPn` and `COM` segment walk, SOF dimensions/components, XMP, IPTC/8BIM datasets, ICC profile text, comments, segment summary, EOI/trailing-byte location |
| EXIF | ASCII, XP, GPS, XMP and UserComment strings from JPEG APP1 and TIFF IFD0, Exif, GPS, Interop and thumbnail IFDs |
| GIF | Header/logical screen, frames, graphic-control, comment, application and plain-text extensions, palette metadata, trailer location |
| BMP | File and DIB headers, dimensions, bit depth, compression, pixel offset, declared end |
| TIFF | Little/big-endian headers, bounded IFD traversal, common string tags and XMP tag `0x02BC` |
| Structure | Prepended bytes, trailing bytes, and polyglot classification by magic |
| Carving | Complete PNG, JPEG, GIF, BMP, ZIP, PDF, gzip, bzip2, xz and WAV containers |
| Compression | Raw DEFLATE streams and zlib streams embedded at bounded offsets |

The flag engine searches the resulting text, metadata and bitstream artifacts
automatically. No image-specific flag options are needed.

## Plugins and options

| Plugin | Purpose | Main options |
|---|---|---|
| `image.structure` | Trailing/prepended bytes and polyglots | `min_bytes=1`, `max_prepend_scan=1048576` |
| `image.png_chunks` | Chunks, text, CRC, palette, iCCP, unknown chunks | `max_text_bytes=1000000`, `max_chunks_in_meta=256`, `emit_unknown_chunks=True`, `max_chunk_bytes=1000000` |
| `image.jpeg` | JPEG segments, XMP, IPTC, ICC, comments | `max_text_bytes=1000000` |
| `image.exif` | EXIF/TIFF strings from nested IFDs, GPS and XMP | none |
| `image.gif` | GIF extensions and frames | `max_text_bytes=1000000` |
| `image.bmp` | BMP/DIB headers | none |
| `image.tiff` | TIFF IFDs, tags and strings | `max_text_bytes=1000000` |
| `image.channels` | Channel, alpha and palette statistics | `max_pixels=16000000`, `emit_filters=True`, `max_filters=3` |
| `image.lsb` | Configurable PNG bit planes | `max_pixels=16000000`, `bits="1"`, `channels="all"`, `order="msb"`, `layout="row-major"`, `visualize=False`, `max_planes=64` |
| `generic.carve` | Magic-based embedded-container carving | `max_input_bytes=16000000`, `max_scan_offset=1000000`, `max_hits=32`, `max_carved=16` |
| `decoder.deflate` | Raw DEFLATE and embedded zlib | `max_input_bytes=8000000`, `max_offsets=64`, `min_output=8`, `max_candidates=3` |

Options are configured under `[plugins.options."plugin.name"]` in `nvox.toml` or
in a scan-specific configuration file. The default `image.lsb` behaviour keeps
the M2 artifact name (`lsb/channel=N`) for bit 1, MSB, row-major extraction.

## Examples

Extract every channel's bit 1 by default:

```bash
nvox scan challenge.png --flag-format 'ctf{}'
```

Try bit planes 1-4 in both bit orders and layouts by adding this to
`nvox.toml` and running the normal scan:

```toml
[plugins.options."image.lsb"]
bits = "1-4"
channels = "all"
order = "msb,lsb"
layout = "row-major,column-major"
visualize = true
max_planes = 128
```

```bash
nvox scan challenge.png --flag-format 'ctf{}'
```

Analyse structure, carve embedded containers and try DEFLATE in one pass:

```bash
nvox scan suspicious.jpg --flag-format 'htb{}' --plugins image.structure,generic.carve,decoder.deflate
```

## Fixtures and coverage

Every row below is generated with the standard library by
`tests/fixture_builders.py`; no binary fixtures or Pillow are required.

| Technique | Fixture / test |
|---|---|
| PNG `tEXt`/`zTXt`/`iTXt` and CRC errors | `make_png_text_chunks`, `test_png_text_chunks_are_parsed`, `test_png_crc_errors_are_reported` |
| PNG unknown chunks and `iCCP` | `make_png_with_unknown_chunk`, `test_png_unknown_chunks_and_iccp` |
| PNG palette, transparency and low bit depth | `make_palette_png`, `make_low_depth_gray_png`, `test_palette_and_low_depth_png` |
| PNG bit planes 1-8, both orders and layouts | `png_with_bitplane`, `test_bitplane_matrix`, `test_bitplane_selection_and_visualisation`, `test_bitplane_option_parsing` |
| JPEG `APPn`/`COM`, XMP, IPTC, ICC | `make_jpeg_with_metadata`, `test_jpeg_segments_xmp_iptc_icc_and_comment` |
| EXIF nested IFDs, GPS and XMP strings | `make_jpeg_with_nested_exif`, `test_exif_nested_ifds_gps_and_xmp_strings` |
| JPEG EOI and trailing data | `make_jpeg`, `test_jpeg_end_stops_at_eoi` |
| GIF extensions and trailing data | `make_gif`, `test_gif_extensions_and_trailing` |
| BMP and TIFF basics, TIFF XMP | `make_bmp`, `make_tiff_with_strings`, `test_bmp_and_tiff_basics` |
| Trailing, prepended and polyglot data | `make_zip`, `make_png`, `make_jpeg`, `test_structure_trailing_prepended_and_polyglot` |
| Embedded PNG/JPEG/GIF/BMP/ZIP/PDF/gzip/bzip2/xz/WAV carving | `test_carver_recognizes_all_declared_containers` |
| Embedded ZIP carving after structure analysis | `test_carve_embedded_zip_after_png` |
| Raw DEFLATE and embedded zlib | `raw_deflate`, `test_deflate_and_embedded_zlib_decoder` |
| End-to-end PNG text and JPEG XMP chains | `test_m3_end_to_end_png_text_chain`, `test_m3_end_to_end_jpeg_xmp_chain` |

Run only the M3 suite:

```bash
.venv/bin/pytest -q tests/test_image_deep.py
```

The full suite covers more than 280 tests, including the M6 audio and generic
fixtures.

## Performance

A 4000x3000 (12 MP) RGB PNG with 2.17 MB of compressed pixel data was scanned
with the default plugin set and `-f 'ctf{}'` on the development NixOS
environment in **48.7 s**, below the default 120 s per-plugin timeout. The scan
emitted channel filters for all three channel pairs; no flag was present.

Larger or noisier images cost more in PNG unfiltering, channel statistics and
flag-surface search. Relevant controls are:

- `image.lsb.max_pixels` and `image.channels.max_pixels` (default 16 MP);
- `image.lsb.max_planes` (default 64) and `bits`/`channels` selections;
- `image.channels.emit_filters` and `max_filters`;
- `generic.carve.max_scan_offset` and `max_carved`;
- `decoder.deflate.max_input_bytes` and `max_offsets`.

To measure a file on your machine:

```bash
time nvox scan large.png -f 'ctf{}' --no-external --out /tmp/nvox-bench
```

Increase `scan.timeout` only when intentionally enabling a more expensive
combination; the per-plugin deadline is cooperative and does not hard-kill a
Python worker.

## Limitations

- PNG decoding covers non-interlaced 1/2/4/8-bit images. Interlaced (Adam7) and
  16-bit PNGs are reported as skips rather than silently mis-decoded.
- Pixel-level bit planes, channel filters and bit-plane previews are implemented
  for PNG. JPEG, GIF, BMP and TIFF support is metadata/structure analysis.
- XMP is preserved as searchable text; Nvox does not build a full XMP/RDF model.
- EXIF numeric and rational tags are not emitted as text; string tags and
  UserComment are.
- Carving recognises a fixed signature set and scans a bounded prefix. It is not
  a replacement for `binwalk`/`foremost`; install those tools to enable the
  optional `bridge.binwalk` and `bridge.foremost` plugins.
- QR/barcode and OCR are not built-in Python implementations. They require the
  optional `zbar` and `tesseract` tools and their bridges; missing tools become
  requirement skips. Both run on emitted image artifacts as well as the original
  evidence.
- A cooperative timeout cannot stop a runaway Python plugin. Command plugins and
  external bridges run in a bounded subprocess with process-group termination;
  optional Python isolation is M7.
