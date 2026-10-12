#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_text, load_job, read_json,
    resolve_workspace, save_job, sha256_text, slug, utc_now, write_jsonl,
)


def unique_output(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}-{index:02d}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def generic_row(item: dict, source: dict, translation: dict) -> dict[str, str]:
    return {
        "Item ID": item["item_id"], "Resource Type": source.get("resource_type", ""),
        "Handle": source.get("external_id", ""), "Source Locale": translation["source_locale"],
        "Target Locale": translation["target_locale"], "Original": source.get("source", ""),
        "Translation": translation["translated_text"], "Source Digest": item["source_digest"],
        "Approved At": item.get("approved_at", ""), "Reviewer": item.get("reviewer", ""),
    }


def csv_content(rows: list[dict[str, str]], fieldnames: list[str]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def shopify_rows(items: list[tuple[dict, dict, dict]]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for item, source, translation in items:
        handle = str(source.get("external_id") or item["item_id"])
        if handle not in grouped:
            grouped[handle] = {
                "Handle": handle, "Title": "", "Body (HTML)": "", "SEO Title": "", "SEO Description": "",
                "BrandLab Item IDs": "", "Source Digests": "", "Target Locale": translation["target_locale"],
            }
            order.append(handle)
        row = grouped[handle]
        resource = source.get("resource_type")
        if resource == "product_title":
            row["Title"] = translation["translated_text"]
        elif resource == "product_description":
            row["Body (HTML)"] = translation["translated_text"]
        elif resource == "seo_title":
            row["SEO Title"] = translation["translated_text"]
        elif resource == "seo_description":
            row["SEO Description"] = translation["translated_text"]
        row["BrandLab Item IDs"] = ", ".join(value for value in [row["BrandLab Item IDs"], item["item_id"]] if value)
        row["Source Digests"] = ", ".join(value for value in [row["Source Digests"], item["source_digest"]] if value)
    return [grouped[handle] for handle in order]


def build_output(items: list[tuple[dict, dict, dict]], fmt: str) -> tuple[str, str, list[str]]:
    if fmt == "csv":
        rows = [generic_row(item, source, translation) for item, source, translation in items]
        fields = ["Item ID", "Resource Type", "Handle", "Source Locale", "Target Locale", "Original", "Translation", "Source Digest", "Approved At", "Reviewer"]
        return csv_content(rows, fields), ".csv", fields
    if fmt == "shopify_csv":
        rows = shopify_rows(items)
        fields = ["Handle", "Title", "Body (HTML)", "SEO Title", "SEO Description", "Target Locale", "BrandLab Item IDs", "Source Digests"]
        return csv_content(rows, fields), ".csv", fields
    if fmt == "json":
        records = [generic_row(item, source, translation) for item, source, translation in items]
        return json.dumps(records, ensure_ascii=False, indent=2) + "\n", ".json", list(records[0].keys()) if records else []
    if fmt == "jsonl":
        records = [generic_row(item, source, translation) for item, source, translation in items]
        return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records), ".jsonl", list(records[0].keys()) if records else []
    if fmt == "markdown":
        chunks = ["# Approved Translations", ""]
        for item, source, translation in items:
            chunks += [f"## `{item['item_id']}`", "", f"- Resource: `{source.get('resource_type')}`", f"- Locale: `{translation['source_locale']} → {translation['target_locale']}`", "", "```text", translation["translated_text"], "```", ""]
        return "\n".join(chunks), ".md", ["item_id", "resource_type", "locale", "translation"]
    if fmt == "html":
        chunks = ["<!doctype html>", "<html><head><meta charset=\"utf-8\"><title>Approved Translations</title></head><body>"]
        for item, source, translation in items:
            safe = html.escape(translation["translated_text"])
            chunks += [f"<section data-item-id=\"{html.escape(item['item_id'])}\">", f"<h2>{html.escape(source.get('resource_type', ''))}</h2>", f"<pre>{safe}</pre>", "</section>"]
        chunks += ["</body></html>", ""]
        return "\n".join(chunks), ".html", ["item_id", "resource_type", "translation"]
    raise BrandLabError(f"--format {fmt}: unsupported format. Use csv, shopify_csv, json, jsonl, markdown, or html.")


def export(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    job_dir, manifest = load_job(workspace, args.job)
    if manifest.get("mode") not in {"pure", "shopify_controlled"}:
        raise BrandLabError(f"{job_dir / 'manifest.json'}: unsupported job mode {manifest.get('mode')}.")
    selected = []
    for item in manifest["items"]:
        if args.ids:
            allowed = {value.strip() for value in args.ids.split(",") if value.strip()}
            if item["item_id"] not in allowed:
                continue
        if item["status"] == "exported" and args.include_exported:
            pass
        elif item["status"] != "approved":
            continue
        selected.append(item)
    if not selected:
        raise BrandLabError("export: no approved items found. Run apply_approval.py first.")

    prepared = []
    for item in selected:
        translation = read_json(job_dir / item["translation_path"])
        source = read_json(job_dir / item["source_path"])
        if translation.get("source_digest") != item["source_digest"]:
            raise BrandLabError(f"export: item {item['item_id']} digest changed. Revalidate before export.")
        prepared.append((item, source, translation))

    content, suffix, fields = build_output(prepared, args.format)
    base = args.output or f"{manifest['job_id']}-{args.format.replace('_', '-')}"
    output_path = Path(base)
    if not output_path.is_absolute():
        output_path = workspace / "exports" / output_path
    if output_path.suffix.casefold() != suffix:
        output_path = output_path.with_suffix(suffix)
    output_path = unique_output(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, content)

    canonical = json.dumps([{
        "item_id": item["item_id"], "source_digest": item["source_digest"],
        "translation": translation["translated_text"],
    } for item, _, translation in prepared], ensure_ascii=False, sort_keys=True)
    export_id = f"{slug(manifest['job_id'])}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    manifest_path = unique_output(output_path.with_suffix(".manifest.json"))
    export_manifest = {
        "export_id": export_id,
        "job_id": manifest["job_id"],
        "format": args.format,
        "locale": manifest["target_locale"],
        "item_count": len(prepared),
        "approved_only": True,
        "fields": fields,
        "created_at": utc_now(),
        "output_path": str(output_path.relative_to(workspace)),
        "manifest_path": str(manifest_path.relative_to(workspace)),
        "content_digest": sha256_text(canonical),
        "item_ids": [item["item_id"] for item, _, _ in prepared],
    }
    atomic_write_text(manifest_path, json.dumps(export_manifest, ensure_ascii=False, indent=2) + "\n")

    for item in manifest["items"]:
        if item["item_id"] in {entry[0]["item_id"] for entry in prepared}:
            item["status"] = "exported"
            item["exported_at"] = export_manifest["created_at"]
            item["export_path"] = str(output_path.relative_to(workspace))
            item["export_id"] = export_id
    manifest["status"] = "exported"
    save_job(job_dir, manifest)
    append_history(workspace, "export_created", {
        "job_id": manifest["job_id"], "export_id": export_id,
        "format": args.format, "item_count": len(prepared), "output_path": str(output_path),
    })
    print(json.dumps({"result": "ok", "export_id": export_id, "items": len(prepared), "output": str(output_path), "manifest": str(manifest_path)}))
    print("Next: import the export into the downstream system. Exported items are recorded in history.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export approved translation items.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--job", required=True)
    parser.add_argument("--format", default="shopify_csv", choices=["csv", "shopify_csv", "json", "jsonl", "markdown", "html"])
    parser.add_argument("--ids", help="Optional comma-separated item ID subset; all must be approved.")
    parser.add_argument("--output", help="Optional output filename relative to brandlab/exports.")
    parser.add_argument("--include-exported", action="store_true", help="Include previously exported approved items.")
    return parser


def main() -> int:
    try:
        export(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
