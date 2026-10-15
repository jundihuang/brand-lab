#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from brandlab_lib import (    read_json_contract,

    BrandLabError, append_history, atomic_write_json, load_job, read_json,
    resolve_workspace, save_job, slug, utc_now,
)
from mutation_lib import load_plan, plan_digest, save_plan
from shopify_connector import ShopifyConnector


DIGEST_QUERY = '''query LatestTranslatableDigest($resourceId: ID!) {
  translatableResource(resourceId: $resourceId) {
    resourceId
    translatableContent {
      key
      digest
    }
  }
}'''


def save_result(workspace: Path, result: dict[str, Any]) -> Path:
    path = workspace / "shopify" / "translation-writeback-results" / f"{result['execution_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, result)
    return path


def fail(workspace: Path, plan_path: Path, plan: dict[str, Any], job_dir: Path, manifest: dict[str, Any], message: str) -> Path:
    execution_id = f"{slug(plan['batch_id'])}_failed_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    result = {
        "execution_id": execution_id, "batch_id": plan["batch_id"], "status": "failed", "created_at": utc_now(),
        "store": plan["store"], "message": message, "raw_output_path": "",
        "items": [{**operation, "status": "failed", "user_errors": []} for operation in plan["operations"]],
    }
    result_path = save_result(workspace, result)
    plan["status"] = "failed"
    plan["executions"].append({"execution_id": execution_id, "status": "failed", "result": str(result_path.relative_to(workspace))})
    save_plan(plan_path, plan)
    for item in manifest["items"]:
        if item["item_id"] in {operation["item_id"] for operation in plan["operations"]}:
            item["status"] = "blocked"
            item["execution_error"] = message
    manifest["status"] = "failed"
    save_job(job_dir, manifest)
    append_history(workspace, "translation_writeback_failed", {
        "batch_id": plan["batch_id"], "execution_id": execution_id, "message": message,
    })
    return result_path


def verify_latest_digests(workspace: Path, plan: dict[str, Any], args: argparse.Namespace) -> None:
    reader = ShopifyConnector(
        workspace, provider=args.provider,
        fixture=Path(args.digest_fixture).expanduser().resolve() if args.digest_fixture else None,
    )
    for resource_id in sorted({operation["shopify_resource_id"] for operation in plan["operations"]}):
        result = reader.run_graphql(
            DIGEST_QUERY, {"resourceId": resource_id}, operation_kind="read",
            operation_id=f"digest_{slug(plan['batch_id'])}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}",
        )
        resource = ShopifyConnector.response_data(result["response"]).get("translatableResource", {})
        contents = resource.get("translatableContent", []) if isinstance(resource, dict) else []
        by_key = {row.get("key"): row.get("digest") for row in contents if isinstance(row, dict)}
        for operation in plan["operations"]:
            if operation["shopify_resource_id"] != resource_id:
                continue
            latest = by_key.get(operation["field"])
            if latest != operation["translatable_content_digest"]:
                raise BrandLabError(
                    f"digest mismatch for {resource_id}:{operation['field']}; expected "
                    f"{operation['translatable_content_digest']}, found {latest}. Re-run discovery/audit and approval."
                )


def execute(args: argparse.Namespace) -> int:
    workspace = resolve_workspace(Path(args.workspace))
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        candidate = workspace / plan_path
        plan_path = candidate if candidate.exists() else plan_path.expanduser().resolve()
    plan = load_plan(plan_path)
    if plan.get("plan_type") != "translation_register":
        raise BrandLabError(f"{plan_path}: use execute_translation_writeback.py only with translation_register plans.")
    if plan.get("status") != "approved" or not isinstance(plan.get("approval"), dict) or plan["approval"].get("approved") is not True:
        raise BrandLabError(f"{plan_path}: translation writeback plan must be approved first.")
    if plan_digest(plan) != plan.get("review_digest") or plan["approval"].get("plan_digest") != plan.get("review_digest"):
        raise BrandLabError(f"{plan_path}: plan changed after approval. Rebuild and approve it again.")
    if not args.execute:
        print(json.dumps({"result": "preflight", "batch_id": plan["batch_id"], "items": len(plan["operations"]), "approved": True}))
        print("Next: add --execute to perform this approved Shopify Translation API batch.")
        return 0
    job_dir, manifest = load_job(workspace, plan["job_id"])
    approval_state = read_json_contract(job_dir / "approvals.json", "approval-state") if (job_dir / "approvals.json").exists() else {"decisions": []}
    decisions = {row.get("item_id"): row for row in approval_state.get("decisions", []) if isinstance(row, dict)}
    for operation in plan["operations"]:
        job_item = next((row for row in manifest["items"] if row["item_id"] == operation["item_id"]), None)
        decision = decisions.get(operation["item_id"])
        if not job_item or job_item.get("status") != "approved" or not decision or decision.get("decision") != "approve":
            result_path = fail(workspace, plan_path, plan, job_dir, manifest, f"{operation['item_id']} is not approved.")
            raise BrandLabError(f"{plan_path}: execution blocked. Result: {result_path}")
        if job_item.get("source_digest") != operation.get("source_digest"):
            result_path = fail(workspace, plan_path, plan, job_dir, manifest, f"{operation['item_id']} approval digest changed.")
            raise BrandLabError(f"{plan_path}: execution blocked. Result: {result_path}")

    verify_latest_digests(workspace, plan, args)
    mutation_connector = ShopifyConnector(
        workspace, provider=args.provider,
        fixture=Path(args.fixture).expanduser().resolve() if args.fixture else None,
        approved_plan={**plan, "status": "approved"},
    )
    if mutation_connector.config.get("allow_mutations") is not True:
        raise BrandLabError(f"{workspace / 'brand.yaml'}: shopify.allow_mutations is false. Explicitly enable mutations after approval.")
    plan["status"] = "executing"
    save_plan(plan_path, plan)
    operation_id = f"translation_{plan['batch_id']}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    try:
        result = mutation_connector.run_graphql(
            plan["graphql"]["query"], plan["graphql"].get("variables", {}),
            operation_kind="mutation", operation_id=operation_id,
        )
    except BrandLabError as exc:
        result_path = fail(workspace, plan_path, plan, job_dir, manifest, str(exc))
        raise BrandLabError(f"{plan_path}: Shopify execution failed. Result: {result_path} Error: {exc}")
    data = mutation_connector.parse_data(result)
    if not isinstance(data, dict):
        result_path = fail(workspace, plan_path, plan, job_dir, manifest, "Shopify response data is missing.")
        raise BrandLabError(f"{plan_path}: execution failed. Result: {result_path}")

    aliases_by_resource: dict[str, str] = {}
    for operation in plan["operations"]:
        aliases_by_resource.setdefault(operation["shopify_resource_id"], f"operation_{len(aliases_by_resource)}")
    item_results: list[dict[str, Any]] = []
    all_success = True
    for operation in plan["operations"]:
        payload = data.get(aliases_by_resource[operation["shopify_resource_id"]])
        errors = payload.get("userErrors", []) if isinstance(payload, dict) else [{"field": ["brandlab"], "message": "Missing GraphQL operation response."}]
        success = isinstance(payload, dict) and not errors
        all_success = all_success and success
        item_results.append({
            **operation, "status": "success" if success else "failed", "user_errors": errors,
            "translated": payload.get("translations") if isinstance(payload, dict) else None,
        })
    execution_id = f"{slug(plan['batch_id'])}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    execution = {
        "execution_id": execution_id, "batch_id": plan["batch_id"], "status": "executed" if all_success else "failed",
        "created_at": utc_now(), "store": plan["store"], "raw_output_path": str(result["output_path"].relative_to(workspace)),
        "items": item_results,
    }
    result_path = save_result(workspace, execution)
    plan["status"] = execution["status"]
    plan["executions"].append({
        "execution_id": execution_id, "status": execution["status"], "result": str(result_path.relative_to(workspace)),
    })
    save_plan(plan_path, plan)
    for item in manifest["items"]:
        matched = next((row for row in item_results if row["item_id"] == item["item_id"]), None)
        if matched:
            item["writeback_status"] = matched["status"]
    manifest["status"] = "approved" if all_success else "failed"
    save_job(job_dir, manifest)
    append_history(workspace, "translation_writeback_executed", {
        "batch_id": plan["batch_id"], "execution_id": execution_id, "status": execution["status"],
        "items": len(item_results), "result": str(result_path.relative_to(workspace)),
    })
    print(json.dumps({"result": execution["status"], "execution_id": execution_id, "items": len(item_results), "result_file": str(result_path)}, ensure_ascii=False))
    print("Next: verify read-back values before treating this writeback as complete.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute an approved Shopify Translation API writeback plan.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--execute", action="store_true", help="Required for the actual mutation; omit for preflight.")
    parser.add_argument("--digest-fixture", help="Read-only fixture used to re-check translatable content digests.")
    parser.add_argument("--fixture", help="Mutation fixture used only in fixture mode.")
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    return parser


def main() -> int:
    try:
        return execute(build_parser().parse_args())
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
