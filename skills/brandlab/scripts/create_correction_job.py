#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, find_market,
    glossary_digest, glossary_for_locale, load_brand, load_guidelines,
    match_guidelines, read_glossary, read_json, resolve_workspace, save_job,
    sha256_text, slug, utc_now,
)


CORRECTION_MODES = {"fix", "retranslate", "rewrite", "localize"}
ACTIONABLE_AUDIT_STATUSES = {"warning", "fail", "missing", "source_missing"}
AUTO_MODE_BY_RECOMMENDATION = {
    "fix": "fix", "retranslate": "retranslate", "rewrite": "rewrite",
    "localize": "localize",
}


def parse_ids(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def guideline_resource_type(item: dict[str, Any]) -> str:
    resource = str(item.get("resource_type", ""))
    field = str(item.get("field_key", ""))
    if field.startswith("seo_"):
        return field
    if field == "description_html":
        return f"{resource}_description"
    if field == "body_html":
        return f"{resource}_body"
    if field == "title":
        return f"{resource}_title"
    return resource


def correction_mode(item: dict[str, Any], requested: str) -> str:
    if requested != "auto":
        return requested
    recommendation = str(item.get("recommendation", ""))
    mode = AUTO_MODE_BY_RECOMMENDATION.get(recommendation)
    if not mode:
        raise BrandLabError(
            f"audit item {item.get('item_id')}: recommendation '{recommendation or 'missing'}' has no automatic correction mode. "
            "Use --mode fix, retranslate, rewrite, or localize."
        )
    return mode


def load_audit_report(workspace: Path, market: str | None, audit: str | None) -> tuple[dict[str, Any], Path]:
    if audit:
        report_path = Path(audit).expanduser().resolve()
    elif market:
        report_path = workspace / "reports" / f"existing-translation-audit-{slug(market)}.json"
    else:
        raise BrandLabError("--audit or --market is required. For example: --market MX.")
    value = read_json(report_path)
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise BrandLabError(f"{report_path}: audit report is invalid. Run audit_existing_translations.py first.")
    return value, report_path


def select_items(report: dict[str, Any], ids: list[str], all_recommended: bool, requested_mode: str) -> list[dict[str, Any]]:
    if bool(ids) == all_recommended:
        raise BrandLabError("selection: use either --ids or --all-recommended, not both.")
    if ids:
        wanted = set(ids)
        selected = [item for item in report["items"] if item.get("item_id") in wanted]
        missing = wanted - {item.get("item_id") for item in selected}
        if missing:
            raise BrandLabError(f"audit {report.get('audit_id')}: item ID(s) {sorted(missing)} were not found.")
    elif all_recommended:
        selected = [item for item in report["items"] if item.get("recommendation") in AUTO_MODE_BY_RECOMMENDATION]
    else:
        raise BrandLabError("selection: choose --ids or --all-recommended so only confirmed items become corrections.")
    selected = [item for item in selected if item.get("status") in ACTIONABLE_AUDIT_STATUSES]
    if not selected:
        raise BrandLabError("selection: no actionable audit item was selected. Items with status 'pass' cannot become corrections.")
    invalid = [
        item.get("item_id") for item in selected
        if requested_mode != "auto" and str(item.get("audit_mode")) == "paired_source" and requested_mode == "rewrite"
    ]
    if invalid:
        raise BrandLabError(f"selection: rewrite requires target_only audit mode; paired item(s) {invalid} must use fix, retranslate, or localize.")
    return selected


def create_job(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    if brand["mode"] not in {"pure", "shopify_controlled"}:
        raise BrandLabError(f"{workspace / 'brand.yaml'}: mode must be pure or shopify_controlled.")
    if args.mode not in {"auto", *CORRECTION_MODES}:
        raise BrandLabError(f"--mode {args.mode}: use auto, fix, retranslate, rewrite, or localize.")

    report, report_path = load_audit_report(workspace, args.market, args.audit)
    selected = select_items(report, parse_ids(args.ids), args.all_recommended, args.mode)
    modes = {correction_mode(item, args.mode) for item in selected}
    if len(modes) > 1:
        raise BrandLabError(f"selection: selected items need multiple modes {sorted(modes)}. Create separate jobs or use --mode.")
    correction_mode_value = next(iter(modes))
    market_id = str(report.get("market") or (args.market or ""))
    market_entry = find_market(brand, market_id)
    target_locale = str(report.get("target_locale") or market_entry["locale"])

    job_stamp = utc_now().replace("+", "z").replace(":", "").replace("-", "", 2)
    job_id = args.job_id or f"correction_{job_stamp}_{slug(market_id)}_{correction_mode_value}"
    job_dir = workspace / "jobs" / job_id
    if job_dir.exists():
        raise BrandLabError(f"{job_dir}: job already exists. Choose another --job-id.")
    for directory in (job_dir / "sources", job_dir / "context", job_dir / "audits", job_dir / "translations"):
        directory.mkdir(parents=True, exist_ok=True)

    guideline_items = load_guidelines(workspace)
    all_glossary = read_glossary(workspace / "glossary" / "glossary.csv")
    job_glossary = glossary_for_locale(all_glossary, target_locale)
    with (job_dir / "context" / "glossary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_term", "target_term", "target_locale", "status", "note"])
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in writer.fieldnames} for row in job_glossary)

    guideline_refs: list[dict[str, Any]] = []
    job_items: list[dict[str, Any]] = []
    for item in selected:
        item_id = str(item["item_id"])
        filter_item = {
            "resource_type": guideline_resource_type(item), "target_locale": target_locale,
            "market": market_id,
        }
        matched = [
            guideline for _, guideline in match_guidelines(guideline_items, filter_item)
            if not args.guideline_id or guideline.get("id") in set(parse_ids(args.guideline_id))
        ]
        guideline_ids = sorted({str(guideline["id"]) for guideline in matched})
        for guideline in matched:
            guideline_id = str(guideline["id"])
            filename = f"{guideline_id}-v{guideline.get('version', 1)}.json"
            if guideline_id not in {ref["id"] for ref in guideline_refs}:
                atomic_write_json(job_dir / "context" / filename, guideline)
                guideline_refs.append({
                    "id": guideline_id, "version": guideline.get("version", 1),
                    "resource_types": [guideline.get("resource_type")],
                    "snapshot": str(Path("context") / filename),
                })

        source_available = item.get("source_available") is True
        reference_text = str(item.get("source_text") or "") if source_available else str(item.get("published_text") or "")
        reference_digest = str(item.get("source_digest") or "") if source_available else (
            item.get("published_digest") or sha256_text(str(item.get("published_text") or ""))
        )
        source_locale = str(item.get("source_locale") or target_locale) if source_available else target_locale
        snapshot = {
            "item_id": item_id, "source": reference_text, "published_text": item.get("published_text", ""),
            "resource_type": guideline_resource_type(item), "original_resource_type": item.get("resource_type", ""),
            "shopify_resource_id": item.get("shopify_resource_id", ""), "field_key": item.get("field_key", ""),
            "source_available": source_available, "audit_mode": item.get("audit_mode"),
            "source_locale": source_locale, "source_digest": reference_digest,
            "translatable_content_digest": item.get("translatable_content_digest"),
            "published_digest": item.get("published_digest"), "target_locale": target_locale,
            "audit": {
                "audit_id": report.get("audit_id"), "status": item.get("status"),
                "recommendation": item.get("recommendation"), "trust_score": item.get("trust_score"),
                "checks": item.get("checks", []), "unverifiable_checks": item.get("unverifiable_checks", []),
                "ai_review": item.get("ai_review"),
            },
        }
        snapshot_path = job_dir / "sources" / f"{item_id}.json"
        atomic_write_json(snapshot_path, snapshot)
        job_items.append({
            "item_id": item_id, "status": "pending", "source_digest": reference_digest,
            "translatable_content_digest": item.get("translatable_content_digest"),
            "guideline_ids": guideline_ids, "source_path": str(Path("sources") / f"{item_id}.json"),
            "translation_path": str(Path("translations") / f"{item_id}.json"),
            "audit_path": str(Path("audits") / f"{item_id}.audit.json"),
            "correction_mode": correction_mode_value,
        })

    manifest = {
        "job_id": job_id, "job_type": "market_translation_correction", "status": "ready", "mode": brand["mode"],
        "audit_id": report.get("audit_id"), "audit_report": str(report_path.relative_to(workspace)),
        "market": market_id, "audit_mode": report.get("audit_mode", "mixed"),
        "correction_mode": correction_mode_value,
        "semantic_validation_available": report.get("source_availability") == "found",
        "source_locale": str(selected[0].get("source_locale") or target_locale) if selected[0].get("source_available") else target_locale,
        "target_locale": target_locale, "guidelines": guideline_refs,
        "glossary_digest": glossary_digest(job_glossary), "created_at": utc_now(), "updated_at": utc_now(),
        "items": job_items,
    }
    save_job(job_dir, manifest)

    plan_lines = [
        "# Market Translation Correction Plan", "",
        f"- Job: `{job_id}`", f"- Audit: `{report.get('audit_id')}`", f"- Market: `{market_id}`",
        f"- Audit mode: `{manifest['audit_mode']}`", f"- Correction mode: `{correction_mode_value}`",
        f"- Semantic validation available: `{manifest['semantic_validation_available']}`", "",
        "| Item | Resource | Field | Status | Recommendation | Mode |", "|---|---|---|---|---|---|",
    ]
    for item in selected:
        plan_lines.append(
            f"| `{item['item_id']}` | `{item.get('resource_type', '')}` | `{item.get('field_key', '')}` | "
            f"{item.get('status')} | {item.get('recommendation')} | {correction_mode_value} |"
        )
    plan_lines += ["", "This plan only creates a local correction job. It did not modify Shopify.", ""]
    plan_path = workspace / "corrections" / f"correction-plan-{slug(market_id)}-{job_id}.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("\n".join(plan_lines), encoding="utf-8")
    append_history(workspace, "correction_job_created", {
        "job_id": job_id, "audit_id": report.get("audit_id"), "market": market_id,
        "correction_mode": correction_mode_value, "items": len(job_items),
    })
    print(json.dumps({
        "result": "ok", "job_id": job_id, "job_type": manifest["job_type"], "market": market_id,
        "correction_mode": correction_mode_value, "items": len(job_items),
        "manifest": str(job_dir / "manifest.json"), "plan": str(plan_path),
    }, ensure_ascii=False))
    print(f"Next: python3 scripts/translate_job.py --workspace {workspace} --job {job_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a correction job from an existing translation audit.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--market", help="Market ID used to locate reports/existing-translation-audit-{market}.json.")
    parser.add_argument("--audit", help="Explicit audit report JSON path.")
    parser.add_argument("--ids", help="Comma-separated audit item IDs selected by the user.")
    parser.add_argument("--all-recommended", action="store_true", help="Select fix/retranslate/rewrite/localize recommendations.")
    parser.add_argument("--mode", default="auto", help="auto, fix, retranslate, rewrite, or localize.")
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
