#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, load_job, read_json,
    resolve_workspace, save_job, utc_now,
)
from mutation_lib import load_plan, save_plan
from shopify_connector import ShopifyConnector


QUERY = '''query VerifyTranslationWriteback($resourceIds: [ID!], $locale: String!) {
  translatableResourcesByIds(first: 10, resourceIds: $resourceIds) {
    nodes {
      resourceId
      translations(locale: $locale) {
        key
        value
        marketId
      }
    }
  }
}'''


def verify(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    result_path = Path(args.result)
    if not result_path.is_absolute():
        candidate = workspace / result_path
        result_path = candidate if candidate.exists() else result_path.expanduser().resolve()
    result = read_json(result_path)
    if result.get("status") != "executed":
        raise BrandLabError(f"{result_path}: only an executed translation writeback can be verified.")
    plan_path = workspace / "shopify" / "translation-writeback-plans" / f"{result['batch_id']}.json"
    plan = load_plan(plan_path)
    if plan.get("plan_type") != "translation_register":
        raise BrandLabError(f"{plan_path}: expected a translation_register plan.")
    connector = ShopifyConnector(workspace, provider=args.provider, fixture=Path(args.fixture).resolve() if args.fixture else None)
    shopify_result = connector.run_graphql(
        QUERY,
        {"resourceIds": sorted({operation["shopify_resource_id"] for operation in plan["operations"]}), "locale": plan["shopify_locale"]},
        operation_kind="read", operation_id=f"verify_translation_{result['execution_id']}",
    )
    nodes = shopify_result["response"].get("data", {}).get("translatableResourcesByIds", {}).get("nodes", [])
    by_resource = {node.get("resourceId"): node for node in nodes if isinstance(node, dict)}
    for item in result["items"]:
        if item.get("status") != "success":
            continue
        node = by_resource.get(item["shopify_resource_id"])
        translations = node.get("translations", []) if isinstance(node, dict) else []
        match = next((row for row in translations if row.get("key") == item["field"]), None)
        value_match = bool(match) and match.get("value") == item["after"]
        market_match = True if item.get("shopify_market_id") is None else match is not None and match.get("marketId") == item["shopify_market_id"]
        item["readback"] = match.get("value") if match else None
        item["status"] = "verified" if value_match and market_match else "mismatched"
    statuses = [item["status"] for item in result["items"]]
    verification_status = "failed" if "failed" in statuses else "mismatched" if "mismatched" in statuses else "verified" if statuses else "failed"
    result["status"] = verification_status
    atomic_write_json(result_path, result)
    for operation in plan["operations"]:
        operation["status"] = next((row["status"] for row in result["items"] if row["item_id"] == operation["item_id"]), operation["status"])
    save_plan(plan_path, plan)
    job_dir, manifest = load_job(workspace, plan["job_id"])
    for job_item in manifest["items"]:
        matched = next((row for row in result["items"] if row["item_id"] == job_item["item_id"]), None)
        if matched:
            job_item["writeback_status"] = matched["status"]
            job_item["verification"] = {"status": matched["status"], "readback": matched.get("readback"), "verified_at": utc_now()}
    manifest["status"] = "approved" if verification_status == "verified" else "failed"
    save_job(job_dir, manifest)
    summary_path = result_path.with_suffix(".verification.md")
    lines = ["# Translation Writeback Verification", "", f"- Execution: `{result['execution_id']}`", f"- Status: `{verification_status}`", "",
             "| Item | Field | Read-back | Status |", "|---|---|---|---|"]
    for item in result["items"]:
        readback = str(item.get("readback", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{item['item_id']}` | `{item['field']}` | {readback} | `{item['status']}` |")
    lines += ["", "This value was read back from the Shopify Translation API view.", ""]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_history(workspace, "translation_writeback_verified", {
        "batch_id": result["batch_id"], "execution_id": result["execution_id"], "status": verification_status,
    })
    print(json.dumps({"result": verification_status, "items": result["items"], "summary": str(summary_path)}, ensure_ascii=False))
    print("Next: all approved translations are verified." if verification_status == "verified" else "Next: investigate the mismatch before running another batch.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read back and verify approved Shopify translations.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--result", required=True)
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    parser.add_argument("--fixture", help="Read-back fixture JSON.")
    return parser


def main() -> int:
    try:
        verify(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
