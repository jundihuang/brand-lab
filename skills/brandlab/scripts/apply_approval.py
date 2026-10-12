#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, load_job, read_json,
    resolve_workspace, save_job, utc_now,
)


DECISIONS = {"approve": "approved", "reject": "rejected", "request_changes": "needs_review"}


def eligible_for_approval(job_item: dict, audit: dict) -> tuple[bool, str]:
    if job_item.get("status") in {"blocked", "pending", "stale"}:
        return False, f"status {job_item.get('status')} is not eligible"
    hard_failures = [
        check for check in audit.get("checks", [])
        if check.get("status") == "failed" and check.get("severity") == "hard"
    ]
    if hard_failures:
        return False, f"{len(hard_failures)} hard-rule failure(s)"
    return True, "eligible"


def apply(args: argparse.Namespace) -> None:
    if args.decision not in DECISIONS:
        raise BrandLabError(f"--decision {args.decision}: use approve, reject, or request_changes.")
    if not args.reviewer:
        raise BrandLabError("--reviewer: reviewer is required for every approval decision.")
    workspace = resolve_workspace(Path(args.workspace))
    job_dir, manifest = load_job(workspace, args.job)
    approval_path = job_dir / "approvals.json"
    approval_state = read_json(approval_path) if approval_path.exists() else {"decisions": []}
    if not isinstance(approval_state, dict) or not isinstance(approval_state.get("decisions"), list):
        raise BrandLabError(f"{approval_path}: approvals must be an object with a decisions list.")

    requested_all = args.item_id == "all"
    requested_ids = None if requested_all else {value.strip() for value in args.item_id.split(",") if value.strip()}
    if requested_all:
        targets = []
        skipped: list[tuple[str, str]] = []
        for job_item in manifest["items"]:
            audit_path = job_dir / job_item["audit_path"]
            audit = read_json(audit_path) if audit_path.exists() else {"checks": []}
            ok, reason = eligible_for_approval(job_item, audit)
            if ok:
                targets.append(job_item)
            else:
                skipped.append((job_item["item_id"], reason))
        if not targets:
            raise BrandLabError("approval: no eligible items found. Review blocked or pending items first.")
    else:
        if not requested_ids:
            raise BrandLabError("--item-id: use all or a comma-separated list of item IDs.")
        known_ids = {item["item_id"] for item in manifest["items"]}
        unknown = requested_ids - known_ids
        if unknown:
            raise BrandLabError(f"--item-id {sorted(unknown)}: item(s) not found in job {args.job}.")
        targets = []
        for job_item in manifest["items"]:
            if job_item["item_id"] not in requested_ids:
                continue
            audit_path = job_dir / job_item["audit_path"]
            audit = read_json(audit_path) if audit_path.exists() else {"checks": []}
            if args.decision == "approve":
                ok, reason = eligible_for_approval(job_item, audit)
                if not ok:
                    raise BrandLabError(f"approval: item {job_item['item_id']} cannot be approved because {reason}.")
            targets.append(job_item)

    new_decisions = []
    for job_item in targets:
        decision = {
            "item_id": job_item["item_id"], "decision": args.decision,
            "recorded_status": DECISIONS[args.decision], "reviewer": args.reviewer,
            "source_digest": job_item["source_digest"], "timestamp": utc_now(),
            "note": args.note,
        }
        approval_state["decisions"].append(decision)
        new_decisions.append(decision)
        job_item["status"] = DECISIONS[args.decision]
        if args.decision == "approve":
            job_item["approved_at"] = decision["timestamp"]
            job_item["reviewer"] = args.reviewer
            job_item.pop("exported_at", None)
        else:
            job_item.pop("approved_at", None)
            job_item.pop("exported_at", None)

    atomic_write_json(approval_path, approval_state)
    statuses = [item["status"] for item in manifest["items"]]
    if "pending" in statuses or "stale" in statuses or "needs_review" in statuses or "blocked" in statuses:
        manifest["status"] = "review"
    elif "approved" in statuses:
        manifest["status"] = "approved"
    else:
        manifest["status"] = "review"
    save_job(job_dir, manifest)
    append_history(workspace, "approval_applied", {
        "job_id": args.job, "decision": args.decision, "reviewer": args.reviewer,
        "items": [item["item_id"] for item in targets],
    })
    print(json.dumps({
        "result": "ok", "job_id": args.job, "decision": args.decision,
        "affected_items": [item["item_id"] for item in targets],
        "job_status": manifest["status"],
    }))
    if requested_all:
        for item_id, reason in skipped:
            print(f"Skipped {item_id}: {reason}", file=sys.stderr)
    print("Next: if ready, run build_export.py to export approved items only.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply an explicit human review decision.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--job", required=True)
    parser.add_argument("--decision", required=True, choices=["approve", "reject", "request_changes"])
    parser.add_argument("--item-id", required=True, help="'all' or comma-separated item IDs.")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--note", default="")
    return parser


def main() -> int:
    try:
        apply(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
