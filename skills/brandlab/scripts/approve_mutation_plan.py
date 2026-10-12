#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brandlab_lib import BrandLabError, append_history, atomic_write_json, resolve_workspace, utc_now
from mutation_lib import load_plan, plan_digest, save_plan


def approve(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        candidate = workspace / plan_path
        plan_path = candidate if candidate.exists() else plan_path.expanduser().resolve()
    plan = load_plan(plan_path)
    if plan.get("status") != "ready_for_review":
        raise BrandLabError(f"{plan_path}: only ready_for_review plans can be approved; current status is {plan.get('status')}.")
    current_digest = plan_digest(plan)
    if plan.get("review_digest") and plan["review_digest"] != current_digest:
        raise BrandLabError(f"{plan_path}: plan changed after review. Regenerate or review the updated plan.")
    plan["review_digest"] = current_digest
    plan["approval"] = {
        "approved": True,
        "reviewer": args.reviewer,
        "approved_at": utc_now(),
        "plan_digest": current_digest,
        "item_ids": [operation["item_id"] for operation in plan["operations"]],
    }
    plan["status"] = "approved"
    save_plan(plan_path, plan)
    append_history(workspace, "shopify_mutation_approved", {
        "batch_id": plan["batch_id"], "reviewer": args.reviewer,
        "plan_digest": current_digest, "item_ids": plan["approval"]["item_ids"],
    })
    print(json.dumps({"result": "ok", "batch_id": plan["batch_id"], "status": "approved", "reviewer": args.reviewer}))
    print("Next: review approval output, then run execute_mutation_plan.py with --execute.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve an already reviewed Shopify mutation plan.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    try:
        approve(args)
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
