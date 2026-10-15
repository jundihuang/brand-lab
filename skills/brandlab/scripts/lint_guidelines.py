#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, atomic_write_text, load_yaml,
    lint_guideline, lint_guideline_cross_file, resolve_workspace, utc_now,
    validate_schema,
)


def lint(args: argparse.Namespace) -> dict:
    workspace = resolve_workspace(Path(args.workspace))
    root = workspace / "guidelines"
    files: list[dict[str, object]] = []
    loaded: list[tuple[Path, dict]] = []
    if not root.exists():
        files.append({"path": str(root), "errors": ["guidelines directory is missing. Run init_workspace.py first."]})
    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        errors: list[str] = []
        value: dict = {}
        try:
            candidate = load_yaml(path)
            if not isinstance(candidate, dict):
                errors.append("guideline must be a YAML mapping. Fix: use top-level key/value fields.")
                candidate = {}
            required = {"id", "name", "resource_type", "target_locale", "status", "version", "rules"}
            missing = sorted(required - set(candidate))
            if missing:
                errors.append(f"missing fields {missing}. Fix: add the missing fields.")
            try:
                validate_schema("guideline", candidate, path)
            except BrandLabError as exc:
                errors.append(str(exc))
            errors.extend(lint_guideline(candidate, path))
            value = candidate
            loaded.append((path, candidate))
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"file cannot be read or parsed. Fix: {exc}")
        files.append({
            "path": str(path), "id": str(value.get("id", "")),
            "status": str(value.get("status", "")), "errors": errors,
        })
    try:
        lint_guideline_cross_file(loaded)
    except BrandLabError as exc:
        files.append({"path": "guidelines", "errors": [str(exc)]})

    report = {
        "generated_at": utc_now(),
        "ok": all(not item["errors"] for item in files),
        "error_count": sum(len(item["errors"]) for item in files),
        "warning_count": 0,
        "files": files,
    }
    output_path = workspace / "reports" / "guideline-lint.json"
    validate_schema("guideline-lint", report, output_path)
    atomic_write_json(output_path, report)
    markdown = [
        "# Guideline Lint", "", f"- Generated at: `{report['generated_at']}`",
        f"- Errors: {report['error_count']}", "", "## Files", "",
        "| File | ID | Status | Errors |", "|---|---|---|---|",
    ]
    for item in files:
        markdown.append(f"| `{item['path']}` | `{item.get('id', '')}` | `{item.get('status', '')}` | {len(item['errors'])} |")
        for error in item["errors"]:
            markdown.append(f"- `{item['path']}`: {error}")
    markdown += ["", "## Next action", "", "Fix each error, then run a translation or correction job."]
    atomic_write_text(workspace / "reports" / "guideline-lint.md", "\n".join(markdown) + "\n")
    append_history(workspace, "guidelines_linted", {"ok": report["ok"], "errors": report["error_count"]})
    print(json.dumps({"result": "ok" if report["ok"] else "invalid", "errors": report["error_count"], "report": str(output_path)}))
    print("Next: fix reported guideline errors before creating a job." if not report["ok"] else "Next: create a translation job when content is ready.")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lint guideline rules before translation.")
    parser.add_argument("--workspace", default="brandlab")
    return parser


def main() -> int:
    try:
        report = lint(build_parser().parse_args())
        return 0 if report["ok"] else 2
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
