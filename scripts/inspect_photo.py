#!/usr/bin/env python3
"""Inspect a local photo without starting Maltego.

Usage:
    .venv/bin/python scripts/inspect_photo.py /path/to/photo.jpg
    .venv/bin/python scripts/inspect_photo.py --raw /path/to/photo.jpg
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from transforms.photo_core import (  # noqa: E402
    ImagePayload,
    analyze_consistency,
    compute_perceptual_hashes,
    extract_photo_facts,
    format_consistency_report,
    format_report,
)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Extract photo metadata with ExifTool")
    parser.add_argument("path", type=Path, help="path to a photo or other image file")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="print the complete ExifTool JSON after the readable report",
    )
    args = parser.parse_args()

    path = args.path.expanduser().resolve()
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1

    payload = ImagePayload(
        data=path.read_bytes(),
        filename=path.name,
        source=str(path),
    )
    facts = await extract_photo_facts(payload)
    hashes = compute_perceptual_hashes(payload)
    report = await analyze_consistency(facts, payload, hashes)
    print(format_report(facts))
    print()
    print(format_consistency_report(report))
    if hashes:
        print()
        print("Perceptual hashes:")
        for algorithm, value in hashes.items():
            print(f"  {algorithm}: {value}")
    if args.raw:
        print("\n--- RAW EXIFTOOL JSON ---")
        print(json.dumps(facts.raw, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception as exc:  # keep the CLI useful when ExifTool rejects a file
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
