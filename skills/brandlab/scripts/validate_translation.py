#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, calculate_trust_score,
    item_status_from_checks, load_glossary_snapshot, load_guideline_snapshots,
    load_job, read_json, resolve_workspace, save_job, utc_now, validate_translation,
)


def revalidate(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    job_dir, manifest = load_job(workspace, args.job)
    guidelines = load_guideline_snapshots(job_dir, manifest)
    glossary = load_glossary_snapshot(job_dir)
    outputs: dict[str, str] = {}
    for item in manifest["items"]:
        path = job_dir / item["translation_path"]
        if path.exists():
            outputs[item["item_id"]] = str(read_json(path).get("translated_text", ""))
    selected = manifest["items"]
    if args.item_id:
        allowed = {value.strip() for value in args.item_id.split(",") if value.strip()}
        selected = [item for item in selected if item["item_id"] in allowed]
        if not selected:
            raise BrandLabError(f"--item-id: no matching item in job {args.job}.")
    updated = 0
    for job_item in selected:
        translation_path = job_dir / job_item["translation_path"]
        if not translation_path.exists():
            raise BrandLabError(f"{translation_path}: translation missing. Run translate_job.py first.")
        translation = read_json(translation_path)
        source = read_json(job_dir / job_item["source_path"])
        item_guidelines = [
            guideline for guideline in guidelines
            if guideline.get("id") in set(job_item.get("guideline_ids") or [])
        ]
        checks = validate_translation(
            source=str(source.get("source", "")), translated=str(translation.get("translated_text", "")),
            source_digest=str(translation.get("source_digest", "")), expected_digest=job_item["source_digest"],
            source_locale=str(translation.get("source_locale", manifest["source_locale"])),
            target_locale=str(translation.get("target_locale", manifest["target_locale"])),
            expected_source_locale=manifest["source_locale"], expected_target_locale=manifest["target_locale"],
            guidelines=item_guidelines, glossary=glossary,
            other_translations={value for key, value in outputs.items() if key != job_item["item_id"]},
        )
        audit = {
            "item_id": job_item["item_id"], "status": item_status_from_checks(checks),
            "trust_score": calculate_trust_score(checks),
            "guideline_versions": {g["id"]: g.get("version") for g in item_guidelines},
            "glossary_digest": manifest.get("glossary_digest"), "checks": checks, "validated_at": utc_now(),
        }
        atomic_write_json(job_dir / job_item["audit_path"], audit)
        previous = job_item["status"]
        job_item["status"] = audit["status"]
        job_item["trust_score"] = audit["trust_score"]
        if previous == "approved" and audit["status"] != "approved":
            append_history(workspace, "approval_invalidated_by_revalidation", {"job_id": args.job, "item_id": job_item["item_id"]})
        updated += 1
    statuses = {item["status"] for item in manifest["items"]}
    manifest["status"] = "translating" if statuses & {"pending", "stale"} else "review"
    save_job(job_dir, manifest)
    append_history(workspace, "translation_revalidated", {"job_id": args.job, "items": updated})
    print(json.dumps({"result": "ok", "job_id": args.job, "validated_items": updated, "statuses": sorted(statuses)}))
    print("Next: run build_review.py after revalidation.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-run deterministic translation validation and refresh audit files.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--job", required=True)
    parser.add_argument("--item-id", help="Optional comma-separated item IDs; defaults to all translated items.")
    args = parser.parse_args()
    try:
        revalidate(args)
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
