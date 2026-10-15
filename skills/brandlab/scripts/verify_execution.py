#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from brandlab_lib import (
    atomic_write_text,
    BrandLabError, append_history, atomic_write_json, load_job, read_json, read_json_contract,
    resolve_workspace, save_job, utc_now, validate_schema,
)
from mutation_lib import load_plan, save_plan
from shopify_connector import ShopifyConnector


FIELD_ACCESS = {
    "title": lambda node: str(node.get("title", "")),
    "descriptionHtml": lambda node: str(node.get("descriptionHtml", "")),
    "seo.title": lambda node: str((node.get("seo") or {}).get("title", "")),
    "seo.description": lambda node: str((node.get("seo") or {}).get("description", "")),
}


def readback_nodes(workspace: Path, plan: dict, result: dict, provider: str, fixture: Path | None) -> dict[str, dict]:
    connector = ShopifyConnector(workspace, provider=provider, fixture=fixture)
    resource_ids = sorted({item["shopify_resource_id"] for item in result["items"] if item["status"] == "success"})
    nodes: dict[str, dict] = {}
    for resource_id in resource_ids:
        query = '''query BrandLabReadBack($id: ID!) {
  product(id: $id) {
    id
    title
    descriptionHtml
    seo { title description }
  }
}'''
        response = connector.run_graphql(
            query, {"id": resource_id}, operation_kind="read",
            operation_id=f"verify_{resource_id.rsplit('/', 1)[-1]}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}",
        )["response"]
        data = response.get("data", {})
        node = data.get("product") if isinstance(data, dict) else None
        if isinstance(node, dict):
            nodes[resource_id] = node
    return nodes


def verify(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    result_path = Path(args.result)
    if not result_path.is_absolute():
        candidate = workspace / result_path
        result_path = candidate if candidate.exists() else result_path.expanduser().resolve()
    result_path = result_path.resolve()
    result = read_json_contract(result_path, "execution-result")
    if result.get("status") not in {"executed", "failed"}:
        raise BrandLabError(f"{result_path}: execution status must be executed or failed.")
    plans_dir = workspace / "shopify" / "mutation-plans"
    plan_path = plans_dir / f"{result['batch_id']}.json"
    plan = load_plan(plan_path)
    if plan.get("batch_id") != result.get("batch_id"):
        raise BrandLabError(f"{plan_path}: batch_id does not match execution result.")
    job_dir, manifest = load_job(workspace, plan.get("job_id", ""))
    job_items = {item["item_id"]: item for item in manifest["items"]}

    if result.get("status") == "failed":
        for item in result["items"]:
            if item.get("status") == "success":
                item["status"] = "failed"
    else:
        nodes = readback_nodes(workspace, plan, result, args.provider, Path(args.fixture).expanduser().resolve() if args.fixture else None)
        for item in result["items"]:
            if item["status"] != "success":
                continue
            node = nodes.get(item["shopify_resource_id"])
            accessor = FIELD_ACCESS.get(item["field"])
            if node is None or accessor is None:
                item["status"] = "mismatched"
                item["readback"] = None
                continue
            readback = accessor(node)
            item["readback"] = readback
            item["status"] = "verified" if readback == item["after"] else "mismatched"

    operation_by_item = {operation["item_id"]: operation for operation in plan["operations"]}
    for item in result["items"]:
        operation = operation_by_item.get(item["item_id"])
        if operation:
            operation["status"] = item["status"]
        job_item = job_items.get(item["item_id"])
        if job_item:
            job_item["status"] = item["status"]
            job_item["verification"] = {
                "status": item["status"], "readback": item.get("readback"), "verified_at": utc_now(),
            }
    statuses = [item["status"] for item in result["items"]]
    verification_status = "failed" if "failed" in statuses else "mismatched" if "mismatched" in statuses else "verified" if statuses else "failed"
    validate_schema("execution-result", result, result_path)
    atomic_write_json(result_path, result)
    save_plan(plan_path, plan)
    if verification_status == "verified":
        manifest["status"] = "approved"
    elif verification_status == "mismatched":
        manifest["status"] = "failed"
    save_job(job_dir, manifest)

    summary_path = result_path.with_suffix(".verification.md")
    lines = [
        "# Shopify Execution Verification", "",
        f"- Execution: `{result['execution_id']}`", f"- Batch: `{result['batch_id']}`",
        f"- Status: `{verification_status}`", "",
        "| Item | Field | Read-back | Status |", "|---|---|---|---|",
    ]
    for item in result["items"]:
        readback = str(item.get("readback", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{item['item_id']}` | `{item['field']}` | {readback} | `{item['status']}` |")
    lines += ["", "## Next action", "", "No later batch may run while a mismatch exists." if verification_status != "verified" else "All executed items verified."]
    atomic_write_text(summary_path, "\n".join(lines) + "\n")
    append_history(workspace, "shopify_execution_verified", {
        "batch_id": result["batch_id"], "execution_id": result["execution_id"],
        "status": verification_status, "result": str(result_path.relative_to(workspace)),
    })
    print(json.dumps({
        "result": verification_status, "batch_id": result["batch_id"], "execution_id": result["execution_id"],
        "items": result["items"], "summary": str(summary_path),
    }, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify executed Shopify mutations with read-back values.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--result", required=True)
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    parser.add_argument("--fixture", help="Fixture JSON with Shopify GraphQL read-back response.")
    args = parser.parse_args()
    try:
        verify(args)
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
