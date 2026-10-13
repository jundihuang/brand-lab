#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, find_market, load_job,
    read_json, resolve_workspace, slug, utc_now,
)
from mutation_lib import approval_digest, plan_digest, TRANSLATION_PLAN_TYPE
from shopify_connector import ShopifyConnector


QUERY = '''mutation BrandLabTranslationRegister($resourceId: ID!, $translations: [TranslationInput!]!) {
  translationsRegister(resourceId: $resourceId, translations: $translations) {
    translations {
      key
      value
      locale
      market { id }
    }
    userErrors {
      field
      message
    }
  }
}'''


def variable_name(resource_id: str) -> str:
    return "resource_" + re.sub(r"[^a-zA-Z0-9]+", "_", resource_id).strip("_").lower()[:32]


def markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def build_plan(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand_data = None
    from brandlab_lib import load_brand
    brand_data = load_brand(workspace)
    job_dir, manifest = load_job(workspace, args.job)
    if manifest.get("job_type") != "market_translation_correction":
        raise BrandLabError(f"{job_dir / 'manifest.json'}: only market_translation_correction jobs can use Translation API writeback.")
    if manifest.get("translation_mode") == "localization_only" or manifest.get("correction_mode") == "localize":
        raise BrandLabError(f"{job_dir / 'manifest.json'}: localization-only corrections need an audit/review workflow, not a translation mutation.")
    if manifest.get("mode") != "shopify_controlled" or manifest.get("status") != "approved":
        raise BrandLabError(f"{job_dir / 'manifest.json'}: job must be approved Shopify Controlled mode before planning writeback.")
    connector = ShopifyConnector(workspace, provider=args.provider, fixture=Path(args.fixture).resolve() if args.fixture else None)
    market_entry = find_market(brand_data, str(manifest["market"]))
    shopify_locale = str(market_entry.get("shopify_locale") or args.shopify_locale or "")
    if not shopify_locale:
        raise BrandLabError(f"{workspace / 'brand.yaml'}: market {manifest['market']} needs shopify_locale for Translation API writeback.")
    approval_path = job_dir / "approvals.json"
    approval_state = read_json(approval_path) if approval_path.exists() else {"decisions": []}

    requested = set(args.ids.split(",")) if args.ids else None
    selected: list[dict[str, Any]] = []
    rejected: list[tuple[str, str]] = []
    for item in manifest["items"]:
        if requested and item["item_id"] not in requested:
            continue
        if item.get("status") != "approved":
            rejected.append((item["item_id"], f"job status {item.get('status')}"))
            continue
        digest = str(item.get("translatable_content_digest") or "").strip()
        if not digest:
            rejected.append((item["item_id"], "missing Shopify translatable content digest"))
            continue
        selected.append(item)
    if requested:
        missing = requested - {item["item_id"] for item in manifest["items"]}
        if missing:
            raise BrandLabError(f"job {args.job}: item ID(s) {sorted(missing)} were not found.")
    if not selected:
        raise BrandLabError(f"job {args.job}: no approved item has a Shopify translatable content digest.")
    if len(selected) > min(int(connector.config["max_batch_size"]), 10):
        raise BrandLabError(f"job {args.job}: selected batch has {len(selected)} items; maximum is 10.")

    variables: dict[str, dict[str, Any]] = {}
    variable_resource_ids: dict[str, str] = {}
    operations: list[dict[str, Any]] = []
    aliases_by_resource: dict[str, str] = {}
    for item in selected:
        snapshot = read_json(job_dir / item["source_path"])
        output = read_json(job_dir / item["translation_path"])
        resource_id = str(snapshot.get("shopify_resource_id") or "")
        if not resource_id.startswith("gid://shopify/"):
            rejected.append((item["item_id"], "invalid Shopify resource ID"))
            continue
        digest = str(item.get("translatable_content_digest") or "").strip()
        if not digest:
            rejected.append((item["item_id"], "missing Shopify translatable content digest"))
            continue
        translation = {
            "key": str(snapshot.get("field_key") or ""),
            "locale": shopify_locale,
            "value": str(output.get("translated_text") or ""),
            "translatableContentDigest": digest,
        }
        if args.shopify_market_id:
            translation["marketId"] = args.shopify_market_id
        if not translation["key"] or not translation["value"]:
            rejected.append((item["item_id"], "missing field key or approved value"))
            continue
        var = variable_name(resource_id)
        alias = aliases_by_resource.setdefault(resource_id, f"operation_{len(aliases_by_resource)}")
        variables.setdefault(var, {"resourceId": resource_id, "translations": []})["translations"].append(translation)
        variable_resource_ids[var] = resource_id
        operations.append({
            "item_id": item["item_id"], "shopify_resource_id": resource_id,
            "resource_type": snapshot.get("resource_type", ""), "field": translation["key"],
            "locale": shopify_locale, "shopify_market_id": args.shopify_market_id,
            "before": str(snapshot.get("published_text") or ""), "after": translation["value"],
            "source_digest": item["source_digest"], "translatable_content_digest": digest,
            "approval_digest": approval_digest(approval_state, item["item_id"]), "status": "planned",
            "rollback_source": str(snapshot.get("published_text") or ""),
        })
    if not operations:
        raise BrandLabError(f"job {args.job}: no writable approved operation was produced.")

    definitions = [f"${name}: ID!" for name in sorted(variables)]
    operation_lines = []
    for var in sorted(variables):
        alias = aliases_by_resource[variables[var]["resourceId"]]
        operation_lines.append(
            f"    {alias}: translationsRegister(resourceId: ${var}, translations: ${var}_translations) "
            "{ translations { key value locale market { id } } userErrors { field message } }"
        )
        definitions.append(f"${var}_translations: [TranslationInput!]!")
    query = "mutation BrandLabTranslationRegister(" + ", ".join(definitions) + ") {\n" + "\n".join(operation_lines) + "\n}"
    validation = connector.validate_graphql(query, mutation=True)
    graphql_variables: dict[str, Any] = {}
    for name, value in variables.items():
        graphql_variables[name] = value["resourceId"]
        graphql_variables[f"{name}_translations"] = value["translations"]

    batch_id = args.batch_id or f"{slug(args.job)}_translation_batch_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    plan = {
        "batch_id": batch_id, "plan_type": TRANSLATION_PLAN_TYPE, "job_id": args.job,
        "mode": "shopify_controlled", "store": connector.config["store"], "status": "ready_for_review",
        "market": manifest["market"], "target_locale": manifest["target_locale"], "shopify_locale": shopify_locale,
        "shopify_market_id": args.shopify_market_id, "max_batch_size": min(int(connector.config["max_batch_size"]), 10),
        "item_count": len(operations), "created_at": utc_now(), "updated_at": utc_now(),
        "graphql": {"query": query, "variables": graphql_variables, "validation": validation},
        "operations": operations, "approval": None, "executions": [],
    }
    plan["review_digest"] = plan_digest(plan)
    plan_dir = workspace / "shopify" / "translation-writeback-plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{batch_id}.json"
    atomic_write_json(plan_path, plan)
    lines = [
        "# Shopify Translation Writeback Plan", "",
        f"- Batch: `{batch_id}`", f"- Job: `{args.job}`", f"- Store: `{connector.config['store']}`",
        f"- Market: `{manifest['market']}`", f"- Shopify locale: `{shopify_locale}`",
        f"- Shopify market ID: `{args.shopify_market_id or 'all markets'}`", f"- Items: {len(operations)}",
        "- This plan did not execute anything.", "",
        "| Item | Resource | Field | Before | After | Digest |", "|---|---|---|---|---|---|",
    ]
    for operation in operations:
        before = markdown_cell(operation["before"])
        after = markdown_cell(operation["after"])
        lines.append(f"| `{operation['item_id']}` | `{operation['shopify_resource_id']}` | `{operation['field']}` | {before} | {after} | `{operation['translatable_content_digest']}` |")
    lines += ["", "## Next action", "", "Review this file, then approve it separately. Execution also requires --execute."]
    review_path = plan_dir / f"{batch_id}-review.md"
    review_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_history(workspace, "translation_writeback_planned", {
        "batch_id": batch_id, "job_id": args.job, "items": len(operations),
        "plan": str(plan_path.relative_to(workspace)), "review": str(review_path.relative_to(workspace)),
    })
    for item_id, reason in rejected:
        print(f"Excluded {item_id}: {reason}", file=sys.stderr)
    print(json.dumps({"result": "ok", "batch_id": batch_id, "status": plan["status"], "items": len(operations), "plan": str(plan_path), "review": str(review_path)}, ensure_ascii=False))
    print("Next: review the plan, then run approve_mutation_plan.py.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a reviewable Shopify Translation API writeback plan.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--job", required=True)
    parser.add_argument("--ids", help="Optional comma-separated approved correction item IDs.")
    parser.add_argument("--batch-id")
    parser.add_argument("--shopify-locale", help="Optional Shopify locale override; defaults to the market registry value.")
    parser.add_argument("--shopify-market-id", help="Optional Shopify market ID for market-specific translation.")
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    parser.add_argument("--fixture", help="Fixture path used only for connector configuration; no request is made.")
    return parser


def main() -> int:
    try:
        build_plan(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
