#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from brandlab_lib import (
    ACTIONABLE_CATALOG_STATUSES, BrandLabError, append_history, atomic_write_json, atomic_write_text,
    glossary_digest, glossary_for_locale, load_brand, load_guidelines, load_glossary_snapshot,
    market_for_locale, match_guidelines, read_catalog, read_glossary, resolve_workspace,
    save_job, slug, utc_now,
)


def parse_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def create_job(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    if brand["mode"] == "shopify_controlled":
        shopify = brand.get("shopify")
        if not isinstance(shopify, dict) or not shopify.get("setup_completed") or not shopify.get("enabled"):
            raise BrandLabError(f"{workspace / 'brand.yaml'}: Shopify setup is incomplete. Run shopify_setup.py.")
    elif brand["mode"] != "pure":
        raise BrandLabError(f"{workspace / 'brand.yaml'}: mode must be pure or shopify_controlled.")
    catalog = read_catalog(workspace)
    target_locale = args.target_locale
    selected = [
        item for item in catalog
        if item.get("target_locale", "").lower() == target_locale.lower()
        and item.get("status") in (set(args.status) if args.status else ACTIONABLE_CATALOG_STATUSES)
    ]
    if args.resource_type:
        selected = [item for item in selected if item.get("resource_type") in set(args.resource_type)]
    if args.ids:
        requested = set(parse_csv(args.ids))
        selected = [item for item in selected if item.get("id") in requested]
    if args.source_file:
        selected = [item for item in selected if item.get("source_file") == args.source_file]
    if not selected:
        raise BrandLabError("catalog: no selectable items. Run import_content.py or widen the filters.")

    guidelines = load_guidelines(workspace)
    matched: list[tuple[Path, dict]] = []
    seen_guideline_ids: set[str] = set()
    for item in selected:
        for path, guideline in match_guidelines(guidelines, item):
            if args.guideline_id and guideline["id"] not in set(parse_csv(args.guideline_id)):
                continue
            if guideline["id"] in seen_guideline_ids:
                continue
            seen_guideline_ids.add(guideline["id"])
            matched.append((path, guideline))

    job_stamp = utc_now().replace("+", "z").replace(":", "").replace("-", "", 2)
    resource_slug = slug(",".join(sorted({item["resource_type"] for item in selected})) or "content")
    job_id = args.job_id or f"{job_stamp}_{target_locale.lower()}_{resource_slug}"
    job_dir = workspace / "jobs" / job_id
    if job_dir.exists():
        raise BrandLabError(f"{job_dir}: job already exists. Choose another --job-id.")

    sources_dir = job_dir / "sources"
    context_dir = job_dir / "context"
    audits_dir = job_dir / "audits"
    translations_dir = job_dir / "translations"
    for directory in (sources_dir, context_dir, audits_dir, translations_dir):
        directory.mkdir(parents=True, exist_ok=True)

    guideline_refs = []
    for path, guideline in matched:
        filename = f"{guideline['id']}-v{guideline.get('version', 1)}.yaml"
        atomic_write_json(context_dir / filename, guideline)
        guideline_refs.append({
            "id": guideline["id"], "version": guideline.get("version", 1),
            "resource_types": [guideline.get("resource_type")],
            "snapshot": str(Path("context") / filename),
        })

    all_glossary = read_glossary(workspace / "glossary" / "glossary.csv")
    job_glossary = glossary_for_locale(all_glossary, target_locale)
    atomic_write_text(
        context_dir / "glossary.csv",
        "source_term,target_term,target_locale,status,note\n" +
        "".join(
            ",".join('"'+str(row.get(field, "")).replace('"', '""')+'"' for field in ["source_term", "target_term", "target_locale", "status", "note"]) + "\n"
            for row in job_glossary
        ),
    )

    job_items = []
    for item in selected:
        item_id = item["id"]
        item_guideline_ids = sorted({
            guideline["id"] for _, guideline in match_guidelines(guidelines, item)
            if not args.guideline_id or guideline["id"] in set(parse_csv(args.guideline_id))
        })
        snapshot_path = sources_dir / f"{item_id}.json"
        atomic_write_json(snapshot_path, item)
        job_items.append({
            "item_id": item_id,
            "status": "pending",
            "source_digest": item["digest"],
            "guideline_ids": item_guideline_ids,
            "source_path": str(snapshot_path.relative_to(job_dir)),
            "translation_path": str(Path("translations") / f"{item_id}.json"),
            "audit_path": str(Path("audits") / f"{item_id}.audit.json"),
        })
        for catalog_item in catalog:
            if catalog_item.get("id") == item_id:
                catalog_item["status"] = "in_job"

    manifest = {
        "job_id": job_id,
        "status": "ready",
        "mode": brand["mode"],
        "source_locale": selected[0].get("source_locale", brand["source_language"]),
        "target_locale": target_locale,
        "market": market_for_locale(brand, target_locale),
        "filters": {
            "resource_types": sorted({item.get("resource_type") for item in selected}),
            "statuses": sorted({item.get("status") for item in selected}),
            "source_files": sorted({item.get("source_file") for item in selected}),
            "guideline_ids": sorted(seen_guideline_ids),
        },
        "guidelines": guideline_refs,
        "glossary_digest": glossary_digest(job_glossary),
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "items": job_items,
    }
    save_job(job_dir, manifest)

    from brandlab_lib import write_catalog
    write_catalog(workspace, catalog)
    append_history(workspace, "job_created", {"job_id": job_id, "item_count": len(job_items), "target_locale": target_locale})
    print(json.dumps({"result": "ok", "job_id": job_id, "items": len(job_items), "manifest": str(job_dir / "manifest.json")}))
    print(f"Next: python3 scripts/translate_job.py --workspace {workspace} --job {job_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a local BrandLab translation job.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--target-locale", required=True)
    parser.add_argument("--resource-type", nargs="*", help="Resource type filter.")
    parser.add_argument("--status", nargs="*", help="Catalog status filter; defaults to new, changed, unchanged, pending.")
    parser.add_argument("--ids", help="Comma-separated catalog item IDs.")
    parser.add_argument("--source-file")
    parser.add_argument("--guideline-id", help="Comma-separated guideline ID allowlist.")
    parser.add_argument("--job-id")
    return parser


def main() -> int:
    try:
        create_job(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
