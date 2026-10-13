#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, load_brand,
    read_json, resolve_workspace, slug, utc_now,
)


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    return read_json(path) if path.is_file() else None


def build_summary(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    markets: list[dict[str, Any]] = []
    for entry in brand.get("markets", []):
        if entry.get("enabled") is not True:
            continue
        market_id = str(entry.get("id") or entry.get("market"))
        market_slug = slug(market_id)
        manifest = load_json_if_exists(workspace / "shopify/existing-translations" / f"{market_slug}-latest.json")
        audit = load_json_if_exists(workspace / "reports" / f"existing-translation-audit-{market_slug}.json")
        jobs = []
        for manifest_path in sorted((workspace / "jobs").glob("*/manifest.json")):
            try:
                job = read_json(manifest_path)
            except BrandLabError:
                continue
            if job.get("market") != market_id:
                continue
            jobs.append({
                "job_id": job.get("job_id"), "job_type": job.get("job_type", "translation"),
                "status": job.get("status"), "correction_mode": job.get("correction_mode"),
                "item_statuses": dict(Counter(item.get("status") for item in job.get("items", []))),
                "writeback_statuses": dict(Counter(item.get("writeback_status") for item in job.get("items", []) if item.get("writeback_status"))),
            })
        plans = []
        for plan_path in sorted((workspace / "shopify/translation-writeback-plans").glob("*.json")):
            try:
                plan = read_json(plan_path)
            except BrandLabError:
                continue
            if plan.get("market") != market_id:
                continue
            plans.append({
                "batch_id": plan.get("batch_id"), "job_id": plan.get("job_id"),
                "status": plan.get("status"), "items": plan.get("item_count"),
                "operation_statuses": dict(Counter(operation.get("status") for operation in plan.get("operations", []))),
            })
        markets.append({
            "market": market_id, "locale": entry.get("locale"), "shopify_locale": entry.get("shopify_locale"),
            "translation_mode": entry.get("translation_mode", "translation"), "audit_mode": entry.get("audit_mode", "auto"),
            "discovery": {
                "discovered": manifest is not None, "source_availability": (manifest or {}).get("source_availability"),
                "record_count": (manifest or {}).get("record_count"),
            },
            "audit": {
                "audited": audit is not None, "audit_id": (audit or {}).get("audit_id"),
                "audit_mode": (audit or {}).get("audit_mode"), "counts": (audit or {}).get("counts", {}),
                "unverifiable_checks": sorted({
                    check for item in (audit or {}).get("items", [])
                    for check in item.get("unverifiable_checks", [])
                }),
            },
            "jobs": jobs, "writeback_plans": plans,
        })

    summary = {"generated_at": utc_now(), "markets": markets}
    output_path = workspace / "reports" / "market-translation-summary.json"
    atomic_write_json(output_path, summary)
    lines = [
        "# Market Translation Summary", "", f"- Generated: `{summary['generated_at']}`", "",
        "| Market | Locale | Workflow | Discovery | Audit | Pass | Warning | Fail | Missing | Source missing | Corrections | Writeback |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in markets:
        counts = row["audit"]["counts"]
        corrections = sum(job.get("item_statuses", {}).get(status, 0) for job in row["jobs"] for status in ["pending", "translated", "needs_review", "approved", "rejected", "blocked"])
        writebacks = sum(job.get("writeback_statuses", {}).get(status, 0) for job in row["jobs"] for status in ["success", "verified", "failed", "mismatched"])
        lines.append(
            f"| `{row['market']}` | `{row['locale']}` | `{row['translation_mode']}` | "
            f"{row['discovery']['record_count'] if row['discovery']['discovered'] else '-'} | "
            f"`{row['audit']['audit_mode'] if row['audit']['audited'] else 'not_audited'}` | "
            f"{counts.get('pass', 0)} | {counts.get('warning', 0)} | {counts.get('fail', 0)} | "
            f"{counts.get('missing', 0)} | {counts.get('source_missing', 0)} | {corrections} | {writebacks} |"
        )
    lines += ["", "## Audit limits", ""]
    limits = [(row["market"], row["audit"]["unverifiable_checks"]) for row in markets if row["audit"]["unverifiable_checks"]]
    if limits:
        for market, checks in limits:
            lines.append(f"- `{market}`: {', '.join(checks)}")
    else:
        lines.append("- No target-only audit limits are active.")
    lines += ["", "This summary reads local workspace state only; it did not call Shopify."]
    markdown_path = workspace / "reports" / "market-translation-summary.md"
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_history(workspace, "market_summary_generated", {"markets": len(markets), "report": str(markdown_path.relative_to(workspace))})
    print(json.dumps({"result": "ok", "markets": len(markets), "json": str(output_path), "report": str(markdown_path)}, ensure_ascii=False))
    print("Next: open the report and prioritize warning, fail, and missing translations.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a cross-market translation summary from local workspace state.")
    parser.add_argument("--workspace", default="brandlab")
    return parser


def main() -> int:
    try:
        build_summary(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
