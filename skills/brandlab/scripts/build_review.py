#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from difflib import unified_diff
from pathlib import Path

from brandlab_lib import resolve_job_path, BrandLabError, append_history, atomic_write_text, load_job, read_json, resolve_workspace, save_job, render_audit_summary


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
    hard_failure_count = 0
    warning_count = 0
    score_total = 0
    scored_count = 0
    for job_item in manifest["items"]:
        item_id = job_item["item_id"]
        status_counts[job_item["status"]] += 1
        source_snapshot = read_json(resolve_job_path(job_dir, job_item["source_path"]))
        source = source_snapshot.get("source", "")
        if resolve_job_path(job_dir, job_item["translation_path"]).is_file():
            translation = read_json(resolve_job_path(job_dir, job_item["translation_path"]))
            output = translation.get("translated_text", "")
        else:
            output = ""
        if resolve_job_path(job_dir, job_item["audit_path"]).is_file():
            audit = read_json(resolve_job_path(job_dir, job_item["audit_path"]))
            checks = audit.get("checks", [])
            score = int(audit.get("trust_score", 0))
            score_total += score
            scored_count += 1
        else:
            checks = []
            score = 0
        hard_failure_count += sum(check.get("status") == "failed" and check.get("severity") == "hard" for check in checks)
        warning_count += sum(check.get("status") == "warning" for check in checks)
        checks_table = ["| Rule | Status | Severity | Message |", "|---|---|---|---|"]
        for check in checks:
            message = str(check.get("message", "")).replace("|", "\\|").replace("\n", " ")
            checks_table.append(f"| `{check['rule_id']}` | {check['status']} | {check['severity']} | {message} |")
        correction = manifest.get("job_type") == "market_translation_correction"
        ai_review = source_snapshot.get("audit", {}).get("ai_review") if correction else None
        correction_lines = [
            f"- Correction mode: `{job_item.get('correction_mode', 'fix')}`",
            f"- Audit mode: `{source_snapshot.get('audit_mode', '')}`",
            f"- Source reference available: `{source_snapshot.get('source_available') is True}`",
            "### Published text", "", "```text", source_snapshot.get("published_text", ""), "```", "",
        ] if correction else []
        if isinstance(ai_review, dict):
            correction_lines.extend([
                "### AI review", "",
                f"- Mode: `{ai_review.get('review_mode', '')}`",
                f"- Model: `{ai_review.get('model', '')}`",
                f"- Confidence: `{ai_review.get('confidence', '')}`", "",
            ])
            for finding in ai_review.get("findings", []):
                correction_lines.append(
                    f"- [{finding.get('severity', '')}] {finding.get('type', '')}: {finding.get('message', '')}"
                )
            suggestion = ai_review.get("suggested_text") or ai_review.get("rewrite_suggestion")
            if suggestion:
                correction_lines.extend(["", "```text", suggestion, "```"])
            correction_lines.append("")
        previous = str(source_snapshot.get("previous_translation") or source_snapshot.get("published_text") or "")
        previous_digest = str(source_snapshot.get("previous_translation_digest") or source_snapshot.get("published_digest") or "")
        digest_state = "current" if job_item.get("source_digest") == source_snapshot.get("digest") else "revalidate"
        context = source_snapshot.get("context") if isinstance(source_snapshot.get("context"), dict) else {}
        field_name = str(
            source_snapshot.get("field_key")
            or source_snapshot.get("field")
            or context.get("field")
            or "content"
        )
        changed_fields = []
        if output != source:
            changed_fields.append(field_name)
        if previous and output != previous:
            changed_fields.append("previous_translation")
        diff_lines = list(unified_diff(
            source.splitlines(), output.splitlines(),
            fromfile="source", tofile="new-translation", lineterm="",
        )) if output else []
        comparison_lines = [
            "### Translation comparison", "",
            f"- Resource type: `{source_snapshot.get('resource_type', '')}`",
            f"- Field: `{field_name}`",
            f"- Target locale: `{manifest['target_locale']}`",
            f"- Source digest: `{job_item.get('source_digest', '')}`",
            f"- Snapshot digest state: `{digest_state}`",
            f"- Previous digest: `{previous_digest}`",
            f"- Changed fields: `{', '.join(changed_fields) if changed_fields else 'none'}`",
            f"- New translation differs from source: `{output != source}`",
            f"- New translation differs from previous: `{output != previous}`", "",
            "### Previous translation", "", "```text", previous, "```", "",
            "### Unified diff", "", "```diff", *diff_lines, "```", "",
        ]
        item_sections.append("\n".join([
            f"## `{item_id}`", "",
            f"- Resource: `{source_snapshot.get('resource_type', '')}`",
            *correction_lines,
            f"- Status: `{job_item['status']}`",
            f"- Trust Score: {score}",
            f"- Suggested decision: {decision(checks)}", "",
            "### Source reference" if correction else "### Source", "", "```text", source, "```", "",
            "### Translation", "", "```text", output, "```", "",
            *comparison_lines,
            "### Checks", "", *checks_table, "",
        ]))

    template = [
        "# Market Translation Correction Review" if manifest.get("job_type") == "market_translation_correction" else "# Translation Review", "",
        f"- Job: `{manifest['job_id']}`",
        f"- Status: `{manifest['status']}`",
        f"- Direction: `{manifest['source_locale']} → {manifest['target_locale']}`",
        *((
            f"- Audit: `{manifest.get('audit_id', '')}`",
            f"- Audit mode: `{manifest.get('audit_mode', '')}`",
            f"- Correction mode: `{manifest.get('correction_mode', '')}`",
            f"- Semantic validation available: `{manifest.get('semantic_validation_available') is True}`",
        ) if manifest.get("job_type") == "market_translation_correction" else ()),
        f"- Glossary digest: `{manifest.get('glossary_digest', '')}`", "",
        "## Summary", "",
        "| Status | Count |", "|---|---:|",
    ]
    for status in ["pending", "translated", "needs_review", "approved", "rejected", "blocked", "exported", "stale"]:
        template.append(f"| `{status}` | {status_counts[status]} |")
    average = round(score_total / scored_count) if scored_count else 0
    template += [
        "", f"- Average Trust Score: {average}",
        f"- Hard failures: {hard_failure_count}", f"- Warnings: {warning_count}", "",
        "## Review policy", "",
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
