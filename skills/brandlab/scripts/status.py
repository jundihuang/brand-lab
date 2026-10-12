#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from brandlab_lib import BrandLabError, load_brand, read_catalog, resolve_workspace


def status(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    catalog = read_catalog(workspace)
    catalog_counts = Counter(str(item.get("status")) for item in catalog)
    locale_counts = Counter(str(item.get("target_locale")) for item in catalog)
    jobs = []
    for manifest_path in sorted((workspace / "jobs").glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            counts = Counter(str(item.get("status")) for item in manifest.get("items", []))
            jobs.append({
                "job_id": manifest.get("job_id"), "status": manifest.get("status"),
                "target_locale": manifest.get("target_locale"), "items": len(manifest.get("items", [])),
                "item_statuses": dict(counts), "manifest": str(manifest_path),
            })
        except (OSError, json.JSONDecodeError):
            continue
    print(json.dumps({
        "workspace": str(workspace), "mode": brand.get("mode"), "catalog_items": len(catalog),
        "catalog_statuses": dict(catalog_counts), "catalog_locales": dict(locale_counts), "jobs": jobs,
    }, ensure_ascii=False, indent=2))
    if not jobs:
        print("Next: import inbox files and create a translation job.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Show BrandLab workspace status.")
    parser.add_argument("--workspace", default="brandlab")
    args = parser.parse_args()
    try:
        status(args)
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
