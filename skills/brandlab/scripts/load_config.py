#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brandlab_lib import BrandLabError, load_brand, load_guidelines, read_glossary, resolve_workspace


def run(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    guidelines = load_guidelines(workspace)
    glossary = read_glossary(workspace / "glossary" / "glossary.csv")
    print(json.dumps({
        "result": "ok", "brand": brand,
        "guidelines": [{"path": str(path), **guideline} for path, guideline in guidelines],
        "glossary_count": len(glossary),
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Load and validate BrandLab configuration, guidelines, and glossary.")
    parser.add_argument("--workspace", default="brandlab")
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
