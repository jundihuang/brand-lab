#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from brandlab_lib import (
    resolve_job_path,
    atomic_write_text,
    BrandLabError, append_history, atomic_write_json, load_brand, load_job,
    read_json, read_json_contract, resolve_workspace, slug, utc_now,
)
from mutation_lib import PRODUCT_FIELD_MAP, approval_digest, plan_digest
from shopify_connector import ShopifyConnector


def parse_ids(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def product_update_input(product_id: str, items: list[dict]) -> tuple[str, dict[str, object]]:
    variable_key = "input_" + product_id.rsplit("/", 1)[-1]
    variable: dict[str, object] = {"id": product_id}
    for job_item in items:
        source = job_item["_source"]
        translation = job_item["_translation"]
        field = source.get("context", {}).get("shopify_field") or PRODUCT_FIELD_MAP[source["resource_type"]]
        if field == "title":
            variable["title"] = translation["translated_text"]
        elif field == "descriptionHtml":
            variable["descriptionHtml"] = translation["translated_text"]
        elif field in {"seo.title", "seo.description"}:
            seo = variable.setdefault("seo", {})
            if isinstance(seo, dict):
                seo[field.split(".", 1)[1]] = translation["translated_text"]
        else:
            raise BrandLabError(
                f"mutation mapping: product field {field} is unsupported. Supported fields are title, descriptionHtml, seo.title, and seo.description."
            )
    return variable_key, variable


def build_plan(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    if brand.get("mode") != "shopify_controlled":
        raise BrandLabError(f"{workspace / 'brand.yaml'}: mutation planning requires shopify_controlled mode.")
    connector = ShopifyConnector(workspace, provider=args.provider, fixture=Path(args.fixture) if args.fixture else None)
    job_dir, manifest = load_job(workspace, args.job)
    if manifest.get("mode") != "shopify_controlled":
        raise BrandLabError(f"{job_dir / 'manifest.json'}: mutation planning requires a shopify_controlled job.")
    approval_state = read_json_contract(job_dir / "approvals.json", "approval-state") if (job_dir / "approvals.json").exists() else {"decisions": []}
    requested_ids = parse_ids(args.ids)
    selected: list[dict] = []
    rejected: list[tuple[str, str]] = []
    for job_item in manifest["items"]:
        if requested_ids and job_item["item_id"] not in requested_ids:
            continue
        if job_item.get("status") != "approved":
            rejected.append((job_item["item_id"], f"status {job_item.get('status')} is not approved"))
            continue
        source = read_json(resolve_job_path(job_dir, job_item["source_path"]))
        resource_type = source.get("resource_type")
        if resource_type not in PRODUCT_FIELD_MAP:
            rejected.append((job_item["item_id"], f"resource_type {resource_type} is not mutation-mappable in MVP"))
            continue
        if not source.get("context", {}).get("shopify_resource_id"):
            rejected.append((job_item["item_id"], "source snapshot has no shopify_resource_id"))
            continue
        if not resolve_job_path(job_dir, job_item["translation_path"]).exists():
            rejected.append((job_item["item_id"], "translation file is missing"))
            continue
        translation = read_json(resolve_job_path(job_dir, job_item["translation_path"]))
        if translation.get("source_digest") != job_item.get("source_digest"):
            rejected.append((job_item["item_id"], "translation source digest does not match job snapshot"))
            continue
        try:
            digest = approval_digest(approval_state, job_item["item_id"])
        except BrandLabError as exc:
            rejected.append((job_item["item_id"], str(exc)))
            continue
        if digest and job_item.get("source_digest") != source.get("digest"):
            rejected.append((job_item["item_id"], "source snapshot digest changed"))
            continue
        enriched = dict(job_item)
        enriched["_source"] = source
        enriched["_translation"] = translation
        enriched["_field"] = source.get("context", {}).get("shopify_field") or PRODUCT_FIELD_MAP[resource_type]
        enriched["_approval_digest"] = digest
        selected.append(enriched)

    if requested_ids:
        unknown = requested_ids - {item["item_id"] for item in manifest["items"]}
        if unknown:
            raise BrandLabError(f"--ids {sorted(unknown)}: item(s) not found in job {args.job}.")
    if not selected:
        detail = "; ".join(f"{item_id}: {reason}" for item_id, reason in rejected) or "no approved items"
        raise BrandLabError(f"mutation plan: no eligible approved items. {detail}")
    if len(selected) > connector.config["max_batch_size"]:
        raise BrandLabError(
            f"mutation plan: {len(selected)} selected items exceed max_batch_size {connector.config['max_batch_size']}. Create another job or select a subset."
        )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in selected:
        grouped[item["_source"]["context"]["shopify_resource_id"]].append(item)
    query_sections = []
    variables: dict[str, object] = {}
    variable_product_ids: dict[str, str] = {}
    plan_operations = []
    for product_id, product_items in sorted(grouped.items()):
        if len(product_items) > 1 and len({item["_source"].get("resource_type") for item in product_items}) > 4:
            raise BrandLabError(f"mutation plan: product {product_id} has unsupported duplicate fields.")
        variable_key, variable = product_update_input(product_id, product_items)
        variable_product_ids[variable_key] = product_id
        variables[variable_key] = variable
        for job_item in product_items:
            before = job_item["_source"].get("source", "")
            plan_operations.append({
                "item_id": job_item["item_id"],
                "shopify_resource_id": product_id,
                "resource_type": "product",
                "field": job_item["_field"],
                "before": before,
                "after": job_item["_translation"]["translated_text"],
                "source_digest": job_item["source_digest"],
                "approval_digest": job_item["_approval_digest"],
                "status": "planned",
                "rollback_source": before,
            })

    # Build one combined GraphQL mutation with aliases and variables.
    definitions = []
    operations = []
    for index, key in enumerate(sorted(variables)):
        definitions.append(f"${key}: ProductInput!")
        operations.append(f"    operation_{index}: productUpdate(input: ${key}) {{ product {{ id title descriptionHtml seo {{ title description }} }} userErrors {{ field message }} }}")
    query = "mutation BrandLabControlledUpdate(" + ", ".join(definitions) + ") {\n" + "\n".join(operations) + "\n  }"
    validation = connector.validate_graphql(query, mutation=True)

    batch_id = args.batch_id or f"{slug(args.job)}_product_batch_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    plan = {
        "batch_id": batch_id,
        "job_id": args.job,
        "mode": "shopify_controlled",
        "store": connector.config["store"],
        "status": "ready_for_review",
        "target_locale": manifest["target_locale"],
        "max_batch_size": connector.config["max_batch_size"],
        "item_count": len(selected),
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "graphql": {"query": query, "variables": variables, "variable_product_ids": variable_product_ids, "validation": validation},
        "operations": plan_operations,
        "approval": None,
        "executions": [],
    }
    plan["plan_digest"] = plan_digest(plan)
    plan_dir = workspace / "shopify" / "mutation-plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{batch_id}.json"
    atomic_write_json(plan_path, plan)

    preview_lines = [
        "# Shopify Mutation Review", "",
        f"- Batch: `{batch_id}`", f"- Job: `{args.job}`", f"- Store: `{connector.config['store']}`",
        f"- Status: `{plan['status']}`", f"- Items: {len(selected)}", f"- Plan digest: `{plan['plan_digest']}`",
        "- This preview did not execute anything.", "",
        "## Affected resources", "",
        "| Item | Resource ID | Field | Before | After |", "|---|---|---|---|---|",
    ]
    for operation in plan_operations:
        before = operation["before"].replace("|", "\\|").replace("\n", " ")
        after = operation["after"].replace("|", "\\|").replace("\n", " ")
        preview_lines.append(f"| `{operation['item_id']}` | `{operation['shopify_resource_id']}` | `{operation['field']}` | {before} | {after} |")
    preview_lines += ["", "## Rollback source", "", "The `before` value in each operation is the exact discovery source for rollback preparation.", ""]
    preview_lines += ["## GraphQL", "", "```graphql", query, "```", ""]
    preview_lines += ["## Variables", "", "```json", json.dumps(variables, ensure_ascii=False, indent=2), "```", "", "## Next action", "", "Review this file, then run approve_mutation_plan.py. Execution remains disabled until approval and a separate --execute command."]
    preview_path = plan_dir / f"{batch_id}-review.md"
    atomic_write_text(preview_path, "\n".join(preview_lines) + "\n")
    append_history(workspace, "shopify_mutation_planned", {
        "batch_id": batch_id, "job_id": args.job, "item_count": len(selected),
        "plan": str(plan_path.relative_to(workspace)), "preview": str(preview_path.relative_to(workspace)),
    })
    if rejected:
        for item_id, reason in rejected:
            print(f"Excluded {item_id}: {reason}", file=sys.stderr)
    print(json.dumps({
        "result": "ok", "batch_id": batch_id, "status": plan["status"], "items": len(selected),
        "plan": str(plan_path), "review": str(preview_path),
    }))
    print("Next: review the plan, then run approve_mutation_plan.py.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a reviewable Shopify mutation plan from approved items.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--job", required=True)
    parser.add_argument("--ids", help="Optional comma-separated approved item IDs.")
    parser.add_argument("--batch-id")
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    parser.add_argument("--fixture", help="Fixture path used only for connector configuration tests; no request is made.")
    parser.add_argument("--preview-only", action="store_true", help="Accepted for compatibility; planning never executes.")
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
