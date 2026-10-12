#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from brandlab_lib import BrandLabError, append_history, atomic_write_text, load_job, read_json, resolve_workspace, save_job, render_audit_summary


def decision(checks: list[dict]) -> str:
    if any(check["status"] == "failed" and check["severity"] == "hard" for check in checks):
        return "BLOCK: fix hard-rule failure"
    if any(check["status"] == "warning" for check in checks):
        return "REVIEW: confirm warnings before approval"
    return "APPROVE: all deterministic checks passed"


def build_review(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    job_dir, manifest = load_job(workspace, args.job)
    item_sections = []
    status_counts: Counter[str] = Counter()
    score_total = 0
    scored_count = 0
    for job_item in manifest["items"]:
        item_id = job_item["item_id"]
        status_counts[job_item["status"]] += 1
        source_snapshot = read_json(job_dir / job_item["source_path"])
        source = source_snapshot.get("source", "")
        if Path(job_item["translation_path"]).is_file():
            translation = read_json(job_dir / job_item["translation_path"])
            output = translation.get("translated_text", "")
        else:
            output = ""
        if Path(job_item["audit_path"]).is_file():
            audit = read_json(job_dir / job_item["audit_path"])
            checks = audit.get("checks", [])
            score = int(audit.get("trust_score", 0))
            score_total += score
            scored_count += 1
        else:
            checks = []
            score = 0
        checks_table = ["| Rule | Status | Severity | Message |", "|---|---|---|---|"]
        for check in checks:
            message = str(check.get("message", "")).replace("|", "\\|").replace("\n", " ")
            checks_table.append(f"| `{check['rule_id']}` | {check['status']} | {check['severity']} | {message} |")
        item_sections.append("\n".join([
            f"## `{item_id}`", "",
            f"- Resource: `{source_snapshot.get('resource_type', '')}`",
            f"- Status: `{job_item['status']}`",
            f"- Trust Score: {score}",
            f"- Suggested decision: {decision(checks)}", "",
            "### Source", "", "```text", source, "```", "",
            "### Translation", "", "```text", output, "```", "",
            "### Checks", "", *checks_table, "",
        ]))

    template = [
        "# Translation Review", "",
        f"- Job: `{manifest['job_id']}`",
        f"- Status: `{manifest['status']}`",
        f"- Direction: `{manifest['source_locale']} → {manifest['target_locale']}`",
        f"- Glossary digest: `{manifest.get('glossary_digest', '')}`", "",
        "## Summary", "",
        "| Status | Count |", "|---|---:|",
    ]
    for status in ["pending", "translated", "needs_review", "approved", "rejected", "blocked", "exported", "stale"]:
        template.append(f"| `{status}` | {status_counts[status]} |")
    average = round(score_total / scored_count) if scored_count else 0
    template += ["", f"- Average Trust Score: {average}", "", "## Review policy", "",
                 "1. Do not approve a hard-rule failure.", "2. Review every warning.", "3. Approval is explicit and recorded with reviewer and source digest.", "",
                 "## Items", "", *item_sections,
                 "## Next action", "",
                 "After review, run apply_approval.py with an explicit decision."]
    review_path = job_dir / "review.md"
    atomic_write_text(review_path, "\n".join(template))
    if manifest["status"] == "translating":
        pending = status_counts["pending"] + status_counts["stale"]
        manifest["status"] = "review" if pending == 0 else "translating"
        save_job(job_dir, manifest)
    append_history(workspace, "review_generated", {"job_id": manifest["job_id"], "path": str(review_path)})
    print(json.dumps({"result": "ok", "job_id": args.job, "review": str(review_path), "statuses": dict(status_counts)}))
    print(f"Next: review {review_path}, then run apply_approval.py.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a human-readable review file.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--job", required=True)
    return parser


def main() -> int:
    try:
        build_review(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
