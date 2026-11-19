# Audio analysis

> **Status:** M6 complete. The built-in audio pack parses WAV containers and
> PCM samples, reads ID3 tags and MPEG frame headers, renders spectrograms,
> decodes DTMF and Morse hints and reports echo-hiding autocorrelation peaks.
> `ffprobe` and SoX add optional coverage through the same bridge contract as
> image tools.

Audio plugins are ordinary `Analyzer` plugins: they receive one artifact and
emit text, metadata, image or byte artifacts. A spectrogram is an image
artifact, so `bridge.tesseract` can OCR it; a decoded DTMF or Morse string is a
text artifact, so the flag scanner searches it without audio-specific options.

## Techniques

| Area | Built-in coverage |
|---|---|
| WAV container | `RIFF/WAVE` header, `fmt ` and `data` chunks, additional chunks, PCM/float/extensible format tags, channels, sample rate, block alignment, bit depth, frame count, duration and bytes after the declared RIFF end |
| WAV sample LSB | One bit from each PCM sample, any whole-byte bit plane, all channels or a selected channel list, MSB/LSB packing |
| MP3 metadata | ID3v2.2/2.3/2.4 frames with text, comments and common metadata, plus the trailing ID3v1 tag |
| MPEG frames | MPEG1/2/2.5 Layer I-III headers, bitrate/sample-rate/channel mode, frame count, duration, sync losses, invalid and truncated headers, mixed streams and trailing bytes |
| Spectrogram | Pure-Python radix-2 FFT over mono PCM, Hann window, configurable FFT size/hop, grayscale PNG with the lowest frequencies at the bottom |
| Tones | DTMF row/column detection with the Goertzel algorithm, Morse on/off keying with adaptive timing and a standard A-Z/0-9/punctuation table |
| Echo hiding | Normalized autocorrelation peaks in configurable lag and threshold ranges, reported as candidate echo delays |

All built-in audio parsing uses the Python standard library. No NumPy, FFmpeg
or SoX installation is required.

## Plugins and options

| Plugin | Purpose | Main options |
|---|---|---|
| `audio.wav` | WAV chunks, format, duration and trailing data | none |
| `audio.wav_lsb` | PCM sample bit planes | `bit=1`, `channels="all"`, `order="msb"`, `max_samples=8000000` |
| `audio.id3` | ID3v2/v1 tags and text | none |
| `audio.mp3_frames` | MPEG frame walk and anomalies | `max_frames=200000` |
| `audio.spectrogram` | Grayscale spectrogram PNG | `fft_size=1024`, `hop=512`, `max_frames=2000`, `max_seconds=120.0`, `max_samples=8000000`, `dynamic_range_db=80.0` |
| `audio.tones` | DTMF and Morse decoding | `max_samples=4000000`, `dtmf=True`, `morse=True` |
| `audio.echo` | Echo-hiding autocorrelation hints | `max_samples=4000000`, `min_lag=20`, `max_lag=1000`, `threshold=0.25` |
| `bridge.ffprobe` | Optional stream/container metadata | none |
| `bridge.sox` | Optional audio statistics | `mode="stat"` or `mode="info"` |

Options are configured under `[plugins.options."plugin.name"]` in `nvox.toml`:

```toml
[plugins.options."audio.wav_lsb"]
bit = 1
channels = "all"
order = "msb"

[plugins.options."audio.spectrogram"]
fft_size = 2048
hop = 512
max_seconds = 60

[plugins.options."audio.echo"]
min_lag = 20
max_lag = 500
threshold = 0.3

[plugins.options."bridge.sox"]
mode = "stat"
```

## Examples

Scan a WAV file with the default audio pack and search for a flag:

```bash
nvox scan challenge.wav --flag-format 'ctf{}'
```

Extract sample LSB from both channels and render spectrograms for visual
inspection:

```toml
[plugins.options."audio.wav_lsb"]
channels = "0,1"
bit = 1
max_samples = 8000000
```

```bash
nvox scan challenge.wav --flag-format 'htb{}' --out out/
```

The spectrogram is written as an artifact and embedded in the HTML report.
If Tesseract is installed, `bridge.tesseract` also runs OCR on the generated
image and the OCR text returns to the artifact graph.

Scan an MP3 without external tools:

```bash
nvox scan song.mp3 --flag-format 'pico{}' --no-external --out out/
```

When FFmpeg and SoX are installed, the same command also runs `bridge.ffprobe`
and `bridge.sox`. Missing tools produce requirement skips and never abort the
scan.

## Fixtures and coverage

Every fixture is generated with the standard library in
`tests/fixture_builders.py`; the suite does not require audio samples or
external binaries.

| Technique | Fixture / test |
|---|---|
| WAV chunks, duration and trailing bytes | `make_wav`, `test_wav_metadata_reports_chunks_duration_and_trailing` |
| WAV sample LSB, bit planes and invalid input | `make_wav_lsb`, `test_wav_lsb_extracts_text_and_respects_bit_option`, `test_wav_helpers_reject_invalid_payloads` |
| ID3 frames, MPEG anomalies and trailing bytes | `make_mp3`, `test_mp3_id3_and_frame_anomalies` |
| Clean frame walks and missing tags | `test_mp3_clean_frames_and_missing_tag_skip` |
| FFT and spectrogram PNG | `test_fft_and_spectrogram_render_a_png` |
| DTMF digits and special keys | `make_dtmf_wav`, `test_dtmf_detection_and_plugin_output` |
| Morse letters, digits and spaces | `make_morse_wav`, `test_morse_detection_and_plugin_output` |
| Echo peaks | `test_echo_hiding_autocorrelation_peaks_and_plugin` |
| ffprobe/SoX bridges | `test_ffprobe_bridge_emits_json_metadata`, `test_sox_bridge_emits_stat_report` |

Run only the M6 audio tests:

```bash
.venv/bin/pytest -o addopts='' -q tests/test_audio_plugins.py
```

## Limitations

- Sample-LSB extraction requires integer PCM with whole-byte samples. Float,
  compressed and ADPCM WAV data is reported as a skip.
- MP3 analysis parses tags and frame headers; it does not decode audio to PCM,
  so MP3 sample-LSB and spectrogram analysis need a decoded WAV. FFmpeg is an
  optional external decoder, not a built-in dependency.
- The spectrogram is intentionally minimal: no axis labels, colour maps or
  frequency zoom. It is still a normal image artifact, so external OCR can read
  it when Tesseract is available.
- DTMF and Morse detection are heuristics tuned for clean CTF fixtures.
  Background noise, overlapping keys and non-standard Morse timing may need
  manual inspection.
- Echo detection reports autocorrelation peaks, not a decoded payload. Strong
  periodic signals (music, tones) can produce candidate peaks too.
- `bridge.ffprobe` and `bridge.sox` are optional. `nvox doctor` shows whether
  they are installed; absent tools become recorded skips.
