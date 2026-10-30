#!/usr/bin/env python3
"""Inspect any local file (image, PDF, Office, generic) without Maltego.

Usage:
    .venv/bin/python scripts/inspect_file.py /path/to/file
    .venv/bin/python scripts/inspect_file.py --raw /path/to/file.pdf
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from transforms.file_core import (  # noqa: E402
    analyze_document_consistency,
    detect_file_kind,
    extract_document_facts,
    format_document_report,
)
from transforms.photo_core import (  # noqa: E402
    ImagePayload,
    analyze_consistency,
    compute_perceptual_hashes,
    extract_photo_facts,
    format_consistency_report,
    format_report,
)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Inspect file metadata")
    parser.add_argument("path", type=Path, help="path to a file")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="print the raw metadata JSON after the readable report",
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
    kind = detect_file_kind(payload)

    if kind == "image":
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
        raw = facts.raw
    else:
        facts = extract_document_facts(payload, kind)
        report = analyze_document_consistency(facts)
        print(format_document_report(facts))
        print()
        print(format_consistency_report(report))
        raw = facts.raw

    if args.raw:
        print("\n--- RAW METADATA JSON ---")
        print(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
