#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brandlab_lib import (
    resolve_job_path,
    BrandLabError, append_history, atomic_write_json, atomic_write_text,
    calculate_trust_score, item_status_from_checks, load_glossary_snapshot,
    load_guideline_snapshots, load_job, read_json, read_json_contract, read_jsonl_contract, resolve_workspace, save_job,
    utc_now, validate_schema, validate_translation, write_jsonl_contract,
)


def render_prompt(manifest: dict, guidelines: list[dict], glossary: list[dict], worklist: list[dict]) -> str:
    guideline_lines = []
    for guideline in guidelines:
        rules = "; ".join(json.dumps(rule, ensure_ascii=False) for rule in guideline.get("rules", []))
        guideline_lines.append(
            f"- `{guideline['id']}` v{guideline.get('version', 1)} | {guideline.get('resource_type')} | {rules}"
        )
    glossary_lines = [
        f"- {row['source_term']} → {row['target_term']} [{row['status']}]{' — ' + row['note'] if row.get('note') else ''}"
        for row in glossary
    ]
    correction = manifest.get("job_type") == "market_translation_correction"
    item_lines = []
    for row in worklist:
        issue_count = len(row.get("audit_issues", []))
        item_lines.append(
            f"- `{row['item_id']}` ({row['resource_type']}): mode={row.get('correction_mode', 'translation')}; "
            f"audit_issues={issue_count}; digest={row['source_digest']}"
        )
        ai_review = row.get("ai_review")
        if isinstance(ai_review, dict):
            item_lines.append(
                f"  - AI review: mode={ai_review.get('review_mode', '')}; "
                f"model={ai_review.get('model', '')}; confidence={ai_review.get('confidence', '')}"
            )
            for finding in ai_review.get("findings", []):
                item_lines.append(
                    f"  - AI finding [{finding.get('severity', '')}] {finding.get('type', '')}: {finding.get('message', '')}"
                )
            suggestion = ai_review.get("suggested_text") or ai_review.get("rewrite_suggestion")
            if suggestion:
                item_lines.append(f"  - AI suggestion: {suggestion}")
    return "\n".join([
        "# Market Translation Correction Prompt" if correction else "# Translation Prompt", "",
        f"- Job: `{manifest['job_id']}`", f"- Direction: {manifest['source_locale']} → {manifest['target_locale']}",
        f"- Workflow: `{manifest.get('correction_mode', 'translation')}`",
        "", "## Brand instructions", "",
        "Correct or rewrite exactly one field at a time. Use the published text and audit issues as context. Preserve source meaning only when a source reference is available; preserve tone, HTML tags, placeholders, and regulated wording."
        if correction else
        "Translate exactly one field at a time. Preserve meaning, tone, HTML tags, placeholders, and regulated wording.",
        "", "## Active guidelines", "", *(guideline_lines or ["- No active guideline matched; do not translate until a guideline is added."]),
        "", "## Glossary", "", *(glossary_lines or ["- No locale glossary entries."]),
        "", "## Items in this worklist", "", *item_lines, "",
        "## Required output", "",
        "Write one JSON object per line to `model-translations.jsonl`:",
        "",
        "```json",
        "{\"item_id\":\"...\",\"translated_text\":\"...\",\"source_digest\":\"sha256:...\",\"target_locale\":\"...\",\"source_locale\":\"...\"}",
        "```",
        "",
        "Do not add comments. Do not omit any item. Do not invent a different source digest.",
    ])


def prepare_translation(workspace: Path, job_dir: Path, manifest: dict) -> tuple[list[dict], str]:
    guidelines = load_guideline_snapshots(job_dir, manifest)
    glossary = load_glossary_snapshot(job_dir)
    worklist: list[dict] = []
    for job_item in manifest["items"]:
        if job_item["status"] not in {"pending", "stale"}:
            continue
        source = read_json(resolve_job_path(job_dir, job_item["source_path"]))
        row = {
            "item_id": job_item["item_id"], "source": source.get("source", ""),
            "source_digest": job_item["source_digest"], "source_locale": manifest["source_locale"],
            "target_locale": manifest["target_locale"], "resource_type": source.get("resource_type", ""),
            "guideline_ids": job_item.get("guideline_ids", []),
        }
        if manifest.get("job_type") == "market_translation_correction":
            audit = source.get("audit", {})
            row.update({
                "published_text": source.get("published_text", ""), "source_available": source.get("source_available") is True,
                "audit_mode": source.get("audit_mode"), "correction_mode": job_item.get("correction_mode", "fix"),
                "audit_issues": audit.get("checks", []), "unverifiable_checks": audit.get("unverifiable_checks", []),
                "ai_review": audit.get("ai_review"),
            })
        worklist.append(row)
    if not worklist:
        raise BrandLabError(f"{manifest['job_id']}: no pending or stale items. Run status.py or create a new job.")
    write_jsonl_contract(job_dir / "worklist.jsonl", worklist, "translation-worklist")
    prompt = render_prompt(manifest, guidelines, glossary, worklist)
    atomic_write_text(job_dir / "translation-prompt.md", prompt + "\n")
    if manifest["status"] in {"ready", "review", "approved"}:
        manifest["status"] = "translating"
    save_job(job_dir, manifest)
    append_history(workspace, "translation_prepared", {"job_id": manifest["job_id"], "item_count": len(worklist)})
    return worklist, str(job_dir / "model-translations.jsonl")


def ingest_translations(workspace: Path, job_dir: Path, manifest: dict, input_path: Path) -> dict[str, int]:
    records = read_jsonl_contract(input_path, "translation-output")
    if not records:
        raise BrandLabError(f"{input_path}: no translation records found. Write one JSON object per line.")
    item_by_id = {row["item_id"]: row for row in manifest["items"]}
    guidelines = load_guideline_snapshots(job_dir, manifest)
    glossary = load_glossary_snapshot(job_dir)
    seen_ids: set[str] = set()
    output_values: dict[str, str] = {}

    # Preload existing outputs for duplicate warning detection.
    existing_outputs: dict[str, str] = {}
    for item in manifest["items"]:
        path = resolve_job_path(job_dir, item["translation_path"])
        if item["item_id"] not in {record.get("item_id") for record in records} and path.exists():
            try:
                existing_outputs[item["item_id"]] = read_json(path).get("translated_text", "")
            except (OSError, json.JSONDecodeError):
                continue

    counts = {"ingested": 0, "blocked": 0, "needs_review": 0, "translated": 0, "skipped": 0, "invalid": 0}
    for line_no, record in enumerate(records, 1):
        item_id = record.get("item_id")
        if not item_id or not isinstance(item_id, str):
            print(f"WARNING: {input_path}:{line_no}: missing item_id; row skipped.", file=sys.stderr)
            counts["invalid"] += 1
            continue
        if item_id in seen_ids:
            raise BrandLabError(f"{input_path}:{line_no}: duplicate item_id {item_id}. Keep one output per item.")
        seen_ids.add(item_id)
        if item_id not in item_by_id:
            print(f"WARNING: {input_path}:{line_no}: item_id {item_id} is not in job {manifest['job_id']}; row skipped.", file=sys.stderr)
            counts["skipped"] += 1
            continue
        job_item = item_by_id[item_id]
        if job_item["status"] not in {"pending", "stale", "blocked", "needs_review", "rejected"}:
            print(f"WARNING: {input_path}:{line_no}: item {item_id} has status {job_item['status']}; row skipped.", file=sys.stderr)
            counts["skipped"] += 1
            continue
        for field in ("translated_text", "source_digest", "target_locale"):
            if field not in record:
                raise BrandLabError(f"{input_path}:{line_no}: record {item_id} is missing {field}. Add the required field and retry.")
        source_snapshot = read_json(resolve_job_path(job_dir, job_item["source_path"]))
        item_guidelines = [
            guideline for guideline in guidelines
            if guideline.get("id") in set(job_item.get("guideline_ids") or [ref["id"] for ref in manifest.get("guidelines", [])])
        ]
        translated_text = str(record["translated_text"])
        source_digest = str(record["source_digest"])
        source_locale = str(record.get("source_locale") or manifest.get("source_locale", ""))
        target_locale = str(record.get("target_locale") or manifest["target_locale"])
        checks = validate_translation(
            source=source_snapshot.get("source", ""), translated=translated_text,
            source_digest=source_digest, expected_digest=job_item["source_digest"],
            source_locale=source_locale, target_locale=target_locale,
            expected_source_locale=manifest["source_locale"], expected_target_locale=manifest["target_locale"],
            guidelines=item_guidelines, glossary=glossary,
            other_translations={value for value in {**existing_outputs, **output_values}.values() if value} - {translated_text},
            resource_type=str(source_snapshot.get("resource_type", "page_body")),
        )
        audit = {
            "item_id": item_id, "status": item_status_from_checks(checks),
            "trust_score": calculate_trust_score(checks),
            "guideline_versions": {guideline["id"]: guideline.get("version") for guideline in item_guidelines},
            "glossary_digest": manifest.get("glossary_digest"), "checks": checks,
        }
        if manifest.get("job_type") == "market_translation_correction":
            audit["correction_mode"] = job_item.get("correction_mode")
            audit["semantic_validation_available"] = manifest.get("semantic_validation_available") is True
        output = {
            "item_id": item_id, "translated_text": translated_text,
            "source_digest": source_digest, "source_locale": source_locale,
            "target_locale": target_locale, "bridge_locale": record.get("bridge_locale"),
            "notes": record.get("notes", ""), "guideline_versions": audit["guideline_versions"],
            "glossary_digest": manifest.get("glossary_digest"), "created_at": utc_now(),
        }
        translation_path = resolve_job_path(job_dir, job_item["translation_path"])
        validate_schema("translation-output", output, translation_path)
        atomic_write_json(translation_path, output)
        audit_path = resolve_job_path(job_dir, job_item["audit_path"])
        validate_schema("audit-result", audit, audit_path)
        atomic_write_json(audit_path, audit)
        previous_status = job_item["status"]
        job_item["status"] = audit["status"]
        job_item["trust_score"] = audit["trust_score"]
        job_item["translated_at"] = utc_now()
        job_item.pop("approved_at", None)
        job_item.pop("reviewer", None)
        counts["ingested"] += 1
        counts[audit["status"]] = counts.get(audit["status"], 0) + 1
        if previous_status == "approved":
            append_history(workspace, "approval_invalidated_by_retranslation", {"job_id": manifest["job_id"], "item_id": item_id})
        output_values[item_id] = translated_text

    has_hard_failure = any(item.get("status") == "blocked" for item in manifest["items"])
    any_pending = any(item.get("status") in {"pending", "stale"} for item in manifest["items"])
    if has_hard_failure or any_pending:
        manifest["status"] = "translating" if any_pending else "review"
    else:
        manifest["status"] = "review"
    save_job(job_dir, manifest)
    append_history(workspace, "translation_ingested", {
        "job_id": manifest["job_id"], "input": str(input_path), "counts": counts,
    })
    return counts


def run(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    job_dir, manifest = load_job(workspace, args.job)
    if manifest.get("mode") not in {"pure", "shopify_controlled"}:
        raise BrandLabError(f"{job_dir / 'manifest.json'}: unsupported job mode {manifest.get('mode')}.")
    if args.input:
        input_path = Path(args.input)
        if not input_path.is_absolute():
            candidate = job_dir / input_path
            input_path = candidate if candidate.exists() else Path(args.input).expanduser().resolve()
        counts = ingest_translations(workspace, job_dir, manifest, input_path)
        print(json.dumps({"result": "ok", "job_id": args.job, "counts": counts}))
        print(f"Next: python3 scripts/build_review.py --workspace {workspace} --job {args.job}")
    else:
        worklist, output_path = prepare_translation(workspace, job_dir, manifest)
        print(json.dumps({"result": "ready", "job_id": args.job, "items": len(worklist), "worklist": str(job_dir / 'worklist.jsonl'), "expected_output": output_path}))
        print(f"Generate {output_path}, then rerun with --input model-translations.jsonl.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Codex translation work or ingest and validate generated output.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--job", required=True)
    parser.add_argument("--input", help="Translation JSONL file; defaults to preparing model work.")
    return parser


def main() -> int:
    try:
        run(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
