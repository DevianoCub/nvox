#!/usr/bin/env python3
"""Run a longer malformed-input pass over the built-in parsers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from fuzz_targets import run_corpus  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=50, help="mutants per seed")
    parser.add_argument("--seed", type=int, default=0xC7F, help="deterministic RNG seed")
    args = parser.parse_args()
    if args.iterations < 0:
        parser.error("--iterations must be zero or greater")
    ran = run_corpus(iterations=args.iterations, seed=args.seed)
    print(f"fuzz: {ran} inputs completed without an unhandled crash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
