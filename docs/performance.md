# Performance and accelerator decision

> **Status:** M7 benchmark pass for 0.1.0.

Nvox prioritises a portable, standard-library core over a native extension at
this stage. This page records the repeatable benchmark and the decision not to
introduce a Rust/PyO3 accelerator for 0.1.0.

## Benchmark method

`scripts/benchmark.py` builds four deterministic inputs and runs the full
built-in plugin graph at depth 3 with one worker:

- `text-64k` — 64 KiB of ASCII text;
- `entropy-1m` — 1 MiB of deterministic high-entropy bytes;
- `png-256` — a 256x256 RGB PNG;
- `zip-8` — a ZIP with eight small text members.

The first pass warms imports and caches; the median of the next repetitions is
reported. External tools are not enabled.

```bash
uv run python scripts/benchmark.py --repeat 3
uv run python scripts/benchmark.py --repeat 3 --json
```

## Reference results

Measured on Linux 7.3.0 x86_64, Python 3.14.8, one worker:

| Scenario | Input | Median | Throughput | Artifacts |
|---|---:|---:|---:|---:|
| `text-64k` | 64 KiB | 0.247 s | 0.25 MiB/s | 69 |
| `entropy-1m` | 1 MiB | 1.432 s | 0.70 MiB/s | 50 |
| `png-256` | 2.2 KiB | 0.809 s | — | 121 |
| `zip-8` | 1.1 KiB | 0.165 s | — | 247 |

Throughput is not the useful metric for tiny structured inputs: the cost is
dominated by plugin dispatch, artifact creation, speculative decoders and
repeat analysis of derived artifacts. These numbers are a regression baseline,
not a promise for other hardware.

## Decision

**No Rust/PyO3 accelerator for 0.1.0.**

Reasons:

1. The measured hot path is a broad graph of small Python plugins, not one
   isolated pixel or byte loop. Moving that graph to a native extension would
   not remove dispatch, store and provenance overhead.
2. The expensive optional paths are already bounded by `max_pixels`,
   `max_planes`, `max_offsets`, archive limits and per-plugin timeouts.
3. A native build artifact would change the installation story for a release
   that currently works with a clean Python install and two small runtime
   dependencies.
4. There is no benchmark evidence that a native extension would improve the
   common CTF-sized input by an order of magnitude.

The decision should be revisited if a profile shows one specific pure-Python
parser dominating realistic workloads after its budgets are applied. A future
accelerator should be an optional wheel, not a mandatory dependency.

## Tuning

Useful controls for large inputs:

- `scan.max_depth`, `scan.timeout`, `scan.max_artifact_bytes` and `scan.jobs`;
- `image.lsb.max_pixels`, `image.lsb.max_planes`, `image.lsb.bits`;
- `image.channels.max_pixels`, `emit_filters`, `max_filters`;
- `generic.carve.max_scan_offset`, `max_carved`;
- `decoder.deflate.max_input_bytes`, `max_offsets`;
- `generic.archive.max_entries`, `max_file_bytes`, `max_total_bytes`,
  `max_ratio`.

For a one-off comparison with external tools disabled:

```bash
time nvox scan large.png -f 'ctf{}' --no-external --out /tmp/nvox-bench
```
