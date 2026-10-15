#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from brandlab_lib import (
    ACTIONABLE_CATALOG_STATUSES, BrandLabError, atomic_write_text, append_history,
    load_brand, read_catalog, read_csv_rows, read_jsonl, resolve_workspace,
    sha256_text, slug, utc_now, write_catalog, write_jsonl_contract,
)


CSV_FIELD_MAPPINGS = {
    "title": "product_title",
    "product title": "product_title",
    "body html": "product_description",
    "body (html)": "product_description",
    "body": "product_description",
    "seo title": "seo_title",
    "seo description": "seo_description",
    "collection title": "collection_title",
    "collection description": "collection_description",
    "page title": "page_title",
    "page body": "page_body",
    "blog title": "blog_title",
    "blog body": "blog_body",
}


def stable_id(*parts: object) -> str:
    identity = "|".join(str(part) for part in parts)
    return "item_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def normalized_key(value: str) -> str:
    return re.sub(r"[\s_()-]+", " ", value.strip()).casefold()


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            try:
                front = yaml.safe_load(text[4:end])
                if isinstance(front, dict):
                    return front, text[end + 5:].lstrip("\n")
            except yaml.YAMLError as exc:
                raise BrandLabError(f"source Markdown: invalid front matter. Fix YAML syntax: {exc}") from exc
    return {}, text


def records_from_csv(path: Path, source_locale: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row_no, row in enumerate(read_csv_rows(path), 2):
        handle = row.get("Handle") or row.get("handle") or ""
        normalized = {normalized_key(key): value for key, value in row.items()}
        explicit_resource = normalized.get("resource type") or normalized.get("resource_type")
        explicit_source = normalized.get("source") or normalized.get("translation source")
        if explicit_source:
            resource = explicit_resource or "page_body"
            records.append({
                "external_id": normalized.get("id") or handle,
                "resource_type": resource,
                "source": explicit_source,
                "context": {"source_row": row_no, "handle": handle},
            })
            continue
        for key, resource_type in CSV_FIELD_MAPPINGS.items():
            value = normalized.get(key, "")
            if not value.strip():
                continue
            records.append({
                "external_id": handle,
                "resource_type": resource_type,
                "source": value,
                "context": {"source_row": row_no, "handle": handle, "field": key},
            })
    return records


def records_from_json(path: Path, source_locale: str) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as exc:
        raise BrandLabError(f"{path}: invalid JSON at line {exc.lineno}. Fix JSON syntax and retry.") from exc
    if isinstance(value, dict):
        value = value.get("items", [value])
    if not isinstance(value, list):
        raise BrandLabError(f"{path}: JSON must be a list, an object with items, or a JSONL file.")
    records: list[dict[str, Any]] = []
    for index, row in enumerate(value, 1):
        if not isinstance(row, dict):
            raise BrandLabError(f"{path}: item {index} must be an object.")
        source = row.get("source")
        resource_type = row.get("resource_type")
        if not source and "title" in row:
            records.append({"external_id": row.get("id", ""), "resource_type": resource_type or "product_title", "source": row["title"], "context": {"row": index}})
        if source:
            records.append({
                "external_id": row.get("id", ""),
                "resource_type": resource_type or "page_body",
                "source": source,
                "context": {"row": index, **{k: v for k, v in row.items() if k not in {"id", "resource_type", "source"}}},
            })
    return records


def records_from_markdown(path: Path) -> list[dict[str, Any]]:
    front, body = parse_front_matter(path.read_text(encoding="utf-8-sig"))
    title = str(front.get("title", "")).strip()
    resource_type = str(front.get("resource_type", "page_body")).strip()
    records: list[dict[str, Any]] = []
    if title:
        title_type = "blog_title" if resource_type.startswith("blog_") else "page_title"
        records.append({"external_id": front.get("id", ""), "resource_type": title_type, "source": title, "context": {"front_matter": front}})
    if body.strip():
        records.append({"external_id": front.get("id", ""), "resource_type": resource_type, "source": body.rstrip(), "context": {"front_matter": front}})
    return records


def records_from_text(path: Path) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8-sig").strip()
    if not source:
        return []
    resource_type = "page_body"
    lowered = path.name.casefold()
    if "care" in lowered:
        resource_type = "care_and_compliance"
    elif "description" in lowered:
        resource_type = "product_description"
    elif "title" in lowered:
        resource_type = "product_title"
    return [{"external_id": path.stem, "resource_type": resource_type, "source": source, "context": {"imported_from": path.name}}]


def read_records(path: Path, source_locale: str) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        return records_from_csv(path, source_locale)
    if suffix in {".json", ".jsonl"}:
        if suffix == ".jsonl":
            records = []
            for row_no, row in enumerate(read_jsonl(path), 1):
                source = row.get("source")
                if not source:
                    raise BrandLabError(f"{path}:{row_no}: JSONL row is missing source.")
                records.append({
                    "external_id": row.get("id", ""), "resource_type": row.get("resource_type", "page_body"),
                    "source": source, "context": {"row": row_no},
                })
            return records
        return records_from_json(path, source_locale)
    if suffix in {".md", ".markdown"}:
        return records_from_markdown(path)
    if suffix in {".txt", ".html", ".htm"}:
        return records_from_text(path)
    raise BrandLabError(f"{path}: unsupported extension {suffix}. Use CSV, JSON, JSONL, Markdown, HTML, or TXT.")


def mark_stale_jobs(workspace: Path, item_id: str, old_digest: str, new_digest: str) -> int:
    changed = 0
    jobs_dir = workspace / "jobs"
    if not jobs_dir.exists():
        return 0
    for manifest_path in jobs_dir.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dirty = False
        for job_item in manifest.get("items", []):
            if job_item.get("item_id") == item_id and job_item.get("source_digest") == old_digest and job_item.get("status") != "stale":
                job_item["status"] = "stale"
                job_item["stale_reason"] = "source_changed"
                dirty = True
                changed += 1
        if dirty:
            manifest["updated_at"] = utc_now()
            atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return changed


def import_files(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    inbox = workspace / "inbox"
    if args.file:
        paths = [workspace / value if not Path(value).is_absolute() else Path(value) for value in args.file]
    else:
        paths = sorted(inbox.glob("**/*")) if inbox.exists() else []
    paths = [path for path in paths if path.is_file()]
    if not paths:
        raise BrandLabError(f"{inbox}: no importable files. Add CSV, JSON, JSONL, Markdown, HTML, or TXT files.")

    target_locales = [value.strip() for value in (args.target_locales or "").split(",") if value.strip()]
    if not target_locales:
        target_locales = [market["locale"] for market in brand.get("markets", []) if market.get("enabled")]
    if not target_locales:
        raise BrandLabError("--target-locales: no target locale supplied and no enabled market found.")

    catalog = read_catalog(workspace)
    by_id = {row["id"]: row for row in catalog if isinstance(row, dict) and row.get("id")}
    counts = {"new": 0, "changed": 0, "unchanged": 0, "skipped": 0}
    imported_rows: list[dict[str, Any]] = []
    source_locale_by_file: dict[str, str] = {}

    for path in paths:
        if args.source_locale:
            file_source_locale = args.source_locale
        else:
            suffix = path.suffix.casefold()
            file_source_locale = ""
            if suffix in {".json", ".jsonl", ".md", ".markdown"}:
                raw = path.read_text(encoding="utf-8-sig")
                try:
                    candidate = json.loads(raw) if suffix in {".json", ".jsonl"} else yaml.safe_load(raw.split("---\n", 2)[1] if raw.startswith("---\n") else {})
                except Exception:
                    candidate = {}
                if isinstance(candidate, dict):
                    file_source_locale = str(candidate.get("source_locale", ""))
            if not file_source_locale:
                raise BrandLabError(f"{path}: source locale is not declared. Add source_locale metadata or pass --source-locale.")
        source_locale_by_file[str(path)] = file_source_locale
        records = read_records(path, file_source_locale)
        if not records:
            counts["skipped"] += 1
        for record in records:
            resource_type = str(record.get("resource_type", "")).strip()
            if resource_type not in {
                "product_title", "product_description", "product_variant", "collection_title",
                "collection_description", "page_title", "page_body", "blog_title", "blog_body",
                "seo_title", "seo_description", "care_and_compliance",
            }:
                raise BrandLabError(
                    f"{path}: resource_type {resource_type} is unsupported. "
                    "Use product_title, product_description, seo_title, seo_description, page_body, blog_body, or care_and_compliance."
                )
            source = str(record.get("source", ""))
            if not source.strip():
                counts["skipped"] += 1
                continue
            digest = sha256_text(source)
            for target_locale in target_locales:
                external_id = str(record.get("external_id") or "")
                identity = [
                    str(path.relative_to(workspace) if path.is_relative_to(workspace) else path),
                    external_id, resource_type, target_locale,
                ]
                item_id = stable_id(*identity)
                old = by_id.get(item_id)
                if old and old.get("digest") == digest:
                    state = "unchanged"
                    old["updated_at"] = utc_now()
                    old["source_file"] = str(path.relative_to(workspace) if path.is_relative_to(workspace) else path)
                    old["context"] = record.get("context", {})
                    old["source_locale"] = file_source_locale
                    old["status"] = "unchanged"
                elif old:
                    state = "changed"
                    old_digest = old["digest"]
                    old["digest"] = digest
                    old["source"] = source
                    old["source_file"] = str(path.relative_to(workspace) if path.is_relative_to(workspace) else path)
                    old["context"] = record.get("context", {})
                    old["source_locale"] = file_source_locale
                    old["status"] = "changed"
                    old["updated_at"] = utc_now()
                    stale_count = mark_stale_jobs(workspace, item_id, old_digest, digest)
                    if stale_count:
                        append_history(workspace, "translation_marked_stale", {"item_id": item_id, "job_items": stale_count})
                else:
                    state = "new"
                    item = {
                        "id": item_id,
                        "external_id": external_id,
                        "source_file": str(path.relative_to(workspace) if path.is_relative_to(workspace) else path),
                        "resource_type": resource_type,
                        "source_locale": file_source_locale,
                        "target_locale": target_locale,
                        "market": next((m["market"] for m in brand.get("markets", []) if m["locale"].lower() == target_locale.lower()), target_locale.split("-")[-1].upper()),
                        "source": source,
                        "context": record.get("context", {}),
                        "digest": digest,
                        "status": "new",
                        "imported_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                    by_id[item_id] = item
                    catalog.append(item)
                counts[state] += 1
                imported_rows.append({
                    "id": item_id, "state": state, "file": str(path), "resource_type": resource_type,
                    "source_locale": file_source_locale, "target_locale": target_locale,
                    "digest": digest,
                })

    write_catalog(workspace, catalog)
    report_path = workspace / "reports" / "import-report.md"
    lines = [
        "# Import Report", "", f"- Generated at: {utc_now()}", f"- Files considered: {len(paths)}",
        f"- New: {counts['new']}", f"- Changed: {counts['changed']}", f"- Unchanged: {counts['unchanged']}",
        f"- Skipped empty or unsupported records: {counts['skipped']}", "", "## Items", "",
        "| ID | State | File | Resource | Source | Target |", "|---|---|---|---|---|---|",
    ]
    for row in imported_rows:
        lines.append(f"| `{row['id']}` | {row['state']} | `{row['file']}` | {row['resource_type']} | {row['source_locale']} | {row['target_locale']} |")
    lines += ["", "## Next action", "", "Run audit_content.py for a target locale, then create a translation job."]
    atomic_write_text(report_path, "\n".join(lines) + "\n")
    write_jsonl_contract(workspace / "reports" / "import-report.jsonl", imported_rows, "import-report")
    append_history(workspace, "content_imported", {"files": [str(path) for path in paths], "counts": counts})
    print(json.dumps({"result": "ok", "counts": counts, "report": str(report_path)}, ensure_ascii=False))
    print("Next: python3 scripts/audit_content.py --workspace " + str(workspace) + " --target-locale " + target_locales[0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import inbox files into the BrandLab content catalog.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--source-locale", help="Source locale for all files; required unless file metadata declares it.")
    parser.add_argument("--target-locales", help="Comma-separated target locales; defaults to enabled brand markets.")
    parser.add_argument("--file", action="append", help="Specific file path; repeat for multiple files. Defaults to all inbox files.")
    return parser


def main() -> int:
    try:
        import_files(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
