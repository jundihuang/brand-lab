#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, load_job, read_json, read_json_contract,
    resolve_workspace, save_job, slug, utc_now, validate_schema,
)
from mutation_lib import approval_digest, load_plan, plan_digest, save_plan
from shopify_connector import ShopifyConnector


def save_result(workspace: Path, result: dict) -> Path:
    path = workspace / "shopify" / "execution-results" / f"{result['execution_id']}.json"
    validate_schema("execution-result", result, path)
    atomic_write_json(path, result)
    return path


def fail_plan(workspace: Path, plan_path: Path, plan: dict, job_dir: Path | None, manifest: dict | None, message: str) -> Path:
    execution_id = f"{slug(plan['batch_id'])}_failed_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    result = {
        "execution_id": execution_id, "batch_id": plan["batch_id"], "status": "failed",
        "created_at": utc_now(), "store": plan["store"], "raw_output_path": "",
        "message": message,
        "items": [{
            "item_id": op["item_id"], "shopify_resource_id": op["shopify_resource_id"],
            "field": op["field"], "after": op["after"], "status": "failed", "user_errors": [],
        } for op in plan["operations"]],
    }
    result_path = save_result(workspace, result)
    plan["status"] = "failed"
    plan["executions"].append({"execution_id": execution_id, "status": "failed", "result": str(result_path.relative_to(workspace))})
    save_plan(plan_path, plan)
    if manifest is not None:
        for op in plan["operations"]:
            for item in manifest["items"]:
                if item["item_id"] == op["item_id"]:
                    item["status"] = "blocked"
                    item["execution_error"] = message
        if job_dir is not None:
            manifest["status"] = "failed"
            save_job(job_dir, manifest)
    append_history(workspace, "shopify_execution_failed", {"batch_id": plan["batch_id"], "execution_id": execution_id, "message": message})
    return result_path


def execute(args: argparse.Namespace) -> int:
    workspace = resolve_workspace(Path(args.workspace))
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        candidate = workspace / plan_path
        plan_path = candidate if candidate.exists() else plan_path.expanduser().resolve()
    plan = load_plan(plan_path)
    if plan.get("status") != "approved":
        raise BrandLabError(f"{plan_path}: plan status must be approved; current status is {plan.get('status')}.")
    approval = plan.get("approval")
    if not isinstance(approval, dict) or approval.get("approved") is not True:
        raise BrandLabError(f"{plan_path}: plan is missing explicit approval.")
    if approval.get("plan_digest") != plan.get("review_digest"):
        raise BrandLabError(f"{plan_path}: plan approval digest does not match the reviewed plan.")
    if plan_digest(plan) != plan.get("review_digest"):
        raise BrandLabError(f"{plan_path}: plan content changed after approval. Rebuild and re-approve the plan.")
    if not args.execute:
        print(json.dumps({"result": "preflight", "batch_id": plan["batch_id"], "items": len(plan["operations"]), "approved": True}))
        print("Next: add --execute to perform this approved batch. No Shopify mutation was made.")
        return
    if len(plan["operations"]) > int(plan["max_batch_size"]) or len(plan["operations"]) > 10:
        raise BrandLabError(f"{plan_path}: batch size {len(plan['operations'])} exceeds maximum 10.")

    if args.fixture and not Path(args.fixture).expanduser().resolve().is_file():
        raise BrandLabError(f"fixture: Shopify fixture not found: {args.fixture}. Pass --fixture with a JSON response file.")

    job_dir, manifest = load_job(workspace, plan["job_id"])
    approval_path = job_dir / "approvals.json"
    approval_state = read_json_contract(approval_path, "approval-state") if approval_path.exists() else {"decisions": []}
    job_items = {item["item_id"]: item for item in manifest["items"]}
    for operation in plan["operations"]:
        job_item = job_items.get(operation["item_id"])
        if not job_item or job_item.get("status") != "approved":
            message = f"{operation['item_id']} is not approved in job {plan['job_id']}."
            result_path = fail_plan(workspace, plan_path, plan, job_dir, manifest, message)
            raise BrandLabError(f"{plan_path}: execution blocked because {message} Result: {result_path}")
        if job_item.get("source_digest") != operation.get("source_digest"):
            message = f"{operation['item_id']} source digest changed after approval."
            result_path = fail_plan(workspace, plan_path, plan, job_dir, manifest, message)
            raise BrandLabError(f"{plan_path}: execution blocked because {message} Result: {result_path}")
        try:
            current_digest = approval_digest(approval_state, operation["item_id"])
        except BrandLabError as exc:
            result_path = fail_plan(workspace, plan_path, plan, job_dir, manifest, str(exc))
            raise BrandLabError(f"{plan_path}: execution blocked because {exc} Result: {result_path}")
        if current_digest != operation.get("approval_digest"):
            message = f"{operation['item_id']} approval digest changed after plan approval."
            result_path = fail_plan(workspace, plan_path, plan, job_dir, manifest, message)
            raise BrandLabError(f"{plan_path}: execution blocked because {message} Result: {result_path}")

    # Construct and preflight the connector before changing the plan to executing.
    connector = ShopifyConnector(
        workspace,
        provider=args.provider,
        fixture=Path(args.fixture).expanduser().resolve() if args.fixture else None,
        approved_plan={**plan, "status": "approved"},
    )
    if connector.config.get("allow_mutations") is not True:
        raise BrandLabError(
            f"{workspace / 'brand.yaml'}: shopify.allow_mutations is false. Explicitly enable mutations after approval."
        )
    if args.provider == "live":
        connector.validate_graphql(plan["graphql"]["query"], mutation=True)
        connector.ensure_supported_flags(mutation=True)
    plan["status"] = "executing"
    save_plan(plan_path, plan)
    operation_id = f"mutation_{plan['batch_id']}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    try:
        result = connector.run_graphql(
            plan["graphql"]["query"], plan["graphql"].get("variables", {}),
            operation_kind="mutation", operation_id=operation_id,
        )
    except BrandLabError as exc:
        result_path = fail_plan(workspace, plan_path, plan, job_dir, manifest, str(exc))
        raise BrandLabError(f"{plan_path}: execution failed. Result: {result_path} Error: {exc}")

    data = result["response"].get("data")
    if not isinstance(data, dict):
        return_path = fail_plan(workspace, plan_path, plan, job_dir, manifest, "Shopify response data is missing.")
        raise BrandLabError(f"{plan_path}: execution failed. Result: {return_path}")

    variable_product_ids = plan["graphql"].get("variable_product_ids", {})
    alias_to_product = {
        f"operation_{index}": variable_product_ids.get(key)
        for index, key in enumerate(sorted(variable_product_ids))
    }
    item_results = []
    user_errors: list[dict] = []
    for operation in plan["operations"]:
        alias = next((alias for alias, product_id in alias_to_product.items() if product_id == operation["shopify_resource_id"]), "")
        payload = data.get(alias) if alias else None
        errors = payload.get("userErrors", []) if isinstance(payload, dict) else [{"field": ["brandlab"], "message": "Missing GraphQL operation response."}]
        returned_product = payload.get("product", {}) if isinstance(payload, dict) else {}
        success = bool(payload) and not errors and returned_product.get("id") == operation["shopify_resource_id"]
        status = "success" if success else "failed"
        item_results.append({
            "item_id": operation["item_id"], "shopify_resource_id": operation["shopify_resource_id"],
            "field": operation["field"], "after": operation["after"], "status": status,
            "user_errors": errors,
        })
        operation["status"] = status
        operation["user_errors"] = errors
        user_errors.extend(errors)
        job_item = job_items[operation["item_id"]]
        if success:
            job_item["status"] = "exported"
            job_item["execution_id"] = operation_id
            job_item["executed_at"] = utc_now()
        else:
            job_item["status"] = "blocked"
            job_item["execution_error"] = json.dumps(errors, ensure_ascii=False)

    execution_id = f"{slug(plan['batch_id'])}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    execution_result = {
        "execution_id": execution_id, "batch_id": plan["batch_id"],
        "status": "failed" if user_errors or any(item["status"] == "failed" for item in item_results) else "executed",
        "created_at": utc_now(), "store": plan["store"],
        "raw_output_path": str(result["output_path"].relative_to(workspace)),
        "command_record_path": "shopify/command-log.jsonl",
        "user_errors": user_errors, "items": item_results,
    }
    result_path = save_result(workspace, execution_result)
    plan["status"] = execution_result["status"]
    plan["executions"].append({
        "execution_id": execution_id, "status": execution_result["status"],
        "result": str(result_path.relative_to(workspace)),
    })
    save_plan(plan_path, plan)
    manifest["status"] = "failed" if execution_result["status"] == "failed" else "exported"
    save_job(job_dir, manifest)
    append_history(workspace, "shopify_execution_completed" if execution_result["status"] == "executed" else "shopify_execution_failed", {
        "batch_id": plan["batch_id"], "execution_id": execution_id,
        "result": str(result_path.relative_to(workspace)), "item_statuses": {
            status: sum(item["status"] == status for item in item_results)
            for status in ["success", "failed"]
        },
    })
    print(json.dumps({"result": execution_result["status"], "batch_id": plan["batch_id"], "execution_id": execution_id, "items": item_results}, ensure_ascii=False))
    if execution_result["status"] == "failed":
        print("Next: fix the blocked items offline. This failed batch was not retried.", file=sys.stderr)
        return 1
    print(f"Next: python3 scripts/verify_execution.py --workspace {workspace} --result {result_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one approved Shopify mutation batch.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--execute", action="store_true", help="Required for actual execution; omitted command performs preflight only.")
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    parser.add_argument("--fixture", help="Fixture JSON with a Shopify GraphQL mutation response; no live CLI call is made.")
    args = parser.parse_args()
    try:
        return execute(args) or 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
