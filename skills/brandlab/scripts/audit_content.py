#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json,
    calculate_trust_score, extract_tags, glossary_for_locale,
    load_guidelines, make_check, match_guidelines, normalize_term, read_catalog,
    read_glossary, resolve_workspace, rule_values, utc_now, write_jsonl,
)


def additional_content_checks(source: str, guidelines: list[dict], target_locale: str, duplicate_count: int = 1) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    base_locale = target_locale.split("-", 1)[0].casefold()
    words = set(re.findall(r"[a-z]+", source.casefold()))
    language_markers = {
        "en": {"the", "and", "with", "from", "made", "for"},
        "it": {"il", "lo", "la", "con", "di", "per", "una", "realizzata"},
        "fr": {"le", "la", "les", "avec", "pour", "une", "fabriquée"},
        "es": {"el", "la", "los", "con", "para", "una", "hecha"},
        "de": {"der", "die", "das", "mit", "für", "aus", "gefertigt"},
    }
    expected = language_markers.get(base_locale, set())
    english = words & language_markers["en"]
    if expected and len(words & expected) == 0 and len(english) >= 2:
        checks.append(make_check("language_mismatch", "warning", "soft", "Content appears to remain in English rather than the target language."))
    else:
        checks.append(make_check("language_mismatch", "passed", "soft"))
    if expected and len(english) >= 2:
        checks.append(make_check("suspected_untranslated_source", "warning", "soft", "English marker words suggest untranslated source text."))
    else:
        checks.append(make_check("suspected_untranslated_source", "passed", "soft"))

    open_braces = source.count("{")
    close_braces = source.count("}")
    percent_placeholders = len(re.findall(r"%[sdif]", source))
    if (open_braces == close_braces and open_braces % 2 == 0) or percent_placeholders:
        checks.append(make_check("placeholder_integrity", "passed", "hard"))
    else:
        checks.append(make_check("placeholder_integrity", "failed", "hard", "Placeholder braces are unbalanced. Fix placeholder syntax."))

    tags = extract_tags(source)
    tag_counts = {}
    for tag in tags:
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
    if all(count % 2 == 0 for count in tag_counts.values()):
        checks.append(make_check("html_structure", "passed", "hard"))
    else:
        checks.append(make_check("html_structure", "failed", "hard", "One or more HTML tags are unbalanced."))

    if duplicate_count > 1:
        checks.append(make_check("repeated_template_pattern", "warning", "soft", f"Exact source text repeats across {duplicate_count} items."))
    else:
        checks.append(make_check("repeated_template_pattern", "passed", "soft"))

    for guideline, rule in rule_values(guidelines, "legal_claim"):
        source_pattern = str(rule.get("source_regex", ""))
        required_pattern = str(rule.get("required_regex", ""))
        try:
            source_hit = bool(re.search(source_pattern, source, flags=re.I))
            target_hit = bool(re.search(required_pattern, source, flags=re.I))
        except re.error as exc:
            raise BrandLabError(f"guideline {guideline['id']}: invalid legal_claim regex. Fix patterns: {exc}") from exc
        checks.append(make_check(
            f"legal_claim:{guideline['id']}", "failed" if source_hit and not target_hit else "passed", "hard",
            "Regulated claim is missing required wording." if source_hit and not target_hit else "Legal claim check passed."
        ))

    return checks


def audit_text(source: str, guidelines: list[dict], glossary: list[dict], target_locale: str, duplicate_count: int = 1) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    checks.append(make_check(
        "empty_content", "failed" if not source.strip() else "passed", "hard",
        "Content is empty." if not source.strip() else "Content is present."
    ))

    translated_terms = set()
    for row in glossary_for_locale(glossary, target_locale):
        if row["status"] == "forbidden" and normalize_term(row["target_term"]) in normalize_term(source):
            translated_terms.add(row["target_term"])
            checks.append(make_check(
                "forbidden_glossary_term", "failed", "hard",
                f"Forbidden term '{row['target_term']}' appears in existing content."
            ))
    if not translated_terms:
        checks.append(make_check("forbidden_glossary_term", "passed", "hard"))

    inconsistent_glossary = False
    for row in glossary_for_locale(glossary, target_locale):
        if row["status"] not in {"locked", "preferred"}:
            continue
        source_term_hit = normalize_term(row["source_term"]) in normalize_term(source)
        target_term_hit = normalize_term(row["target_term"]) in normalize_term(source)
        if source_term_hit and target_term_hit:
            inconsistent_glossary = True
            checks.append(make_check(
                "glossary_consistency", "warning", "soft",
                f"Content mixes source term '{row['source_term']}' and target term '{row['target_term']}'."
            ))
            break
    if not inconsistent_glossary:
        checks.append(make_check("glossary_consistency", "passed", "soft"))

    forbidden_patterns = []
    for guideline, rule in rule_values(guidelines, "forbidden_regex"):
        pattern = str(rule.get("pattern", ""))
        forbidden_patterns.append(pattern)
        try:
            hit = re.search(pattern, source, flags=re.I)
        except re.error as exc:
            raise BrandLabError(f"guideline {guideline['id']}: invalid forbidden_regex. Fix pattern {pattern}: {exc}") from exc
        checks.append(make_check(
            f"forbidden_regex:{guideline['id']}", "failed" if hit else "passed", "hard",
            f"Forbidden pattern matched '{hit.group(0)}'." if hit else "No forbidden pattern matched."
        ))

    required_patterns = []
    for guideline, rule in rule_values(guidelines, "required_regex"):
        pattern = str(rule.get("pattern", ""))
        required_patterns.append(pattern)
        try:
            hit = bool(re.search(pattern, source, flags=re.I))
        except re.error as exc:
            raise BrandLabError(f"guideline {guideline['id']}: invalid required_regex. Fix pattern {pattern}: {exc}") from exc
        checks.append(make_check(
            f"required_regex:{guideline['id']}", "passed" if hit else "failed", "hard",
            "Required pattern is present." if hit else f"Required pattern '{pattern}' is missing."
        ))

    max_rules = [int(rule["value"]) for _, rule in rule_values(guidelines, "max_length") if isinstance(rule.get("value"), int)]
    if max_rules:
        limit = min(max_rules)
        checks.append(make_check(
            "max_length", "passed" if len(source) <= limit else "warning", "soft",
            f"Content is {len(source)} characters; guideline maximum is {limit}."
        ))
    min_rules = [int(rule["value"]) for _, rule in rule_values(guidelines, "min_length") if isinstance(rule.get("value"), int)]
    if min_rules:
        minimum = max(min_rules)
        checks.append(make_check(
            "min_length", "passed" if len(source.strip()) >= minimum else "failed", "hard",
            f"Content is {len(source.strip())} characters; guideline minimum is {minimum}."
        ))

    checks.extend(additional_content_checks(source, guidelines, target_locale, duplicate_count))
    missing_translation = not source.strip()
    checks.append(make_check(
        "missing_translation", "warning", "soft",
        "No translated output exists for this catalog item." if missing_translation else "Translated output exists or is managed by a job."
    ))
    return checks


def audit(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    target_locale = args.target_locale
    catalog = [item for item in read_catalog(workspace) if item.get("target_locale", "").lower() == target_locale.lower()]
    if args.resource_type:
        catalog = [item for item in catalog if item.get("resource_type") in set(args.resource_type)]
    if args.ids:
        requested = [value.strip() for value in args.ids.split(",") if value.strip()]
        catalog = [item for item in catalog if item.get("id") in requested]
    if not catalog:
        raise BrandLabError(f"catalog: no items found for target_locale {target_locale}. Import content first.")

    guidelines = load_guidelines(workspace)
    glossary_rows = read_glossary(workspace / "glossary" / "glossary.csv")
    results: list[dict[str, object]] = []
    source_counts = Counter(normalize_term(item.get("source", "")) for item in catalog if item.get("source", "").strip())
    for item in catalog:
        item_guidelines = [guideline for _, guideline in match_guidelines(guidelines, item)]
        checks = audit_text(item.get("source", ""), item_guidelines, glossary_rows, target_locale, source_counts[normalize_term(item.get("source", ""))])
        if item.get("resource_type") == "seo_title":
            checks.append(make_check("seo_title_length", "passed" if len(item.get("source", "")) <= 70 else "warning", "soft", f"SEO title is {len(item.get('source', ''))} characters; recommended maximum is 70."))
        if item.get("resource_type") == "seo_description":
            checks.append(make_check("seo_description_length", "passed" if len(item.get("source", "")) <= 160 else "warning", "soft", f"SEO description is {len(item.get('source', ''))} characters; recommended maximum is 160."))
        failed = any(check["status"] == "failed" for check in checks)
        warnings = any(check["status"] == "warning" for check in checks)
        status = "blocked" if failed else "needs_review" if warnings else "ok"
        result = {
            "item_id": item["id"], "status": status, "trust_score": calculate_trust_score(checks),
            "resource_type": item.get("resource_type"), "target_locale": target_locale,
            "guideline_versions": {g["id"]: g.get("version") for g in item_guidelines},
            "checks": checks, "recommendation": "translate" if missing_check(checks) else "review" if warnings or failed else "ignore",
        }
        results.append(result)

    output_json = workspace / "reports" / "content-audit.jsonl"
    write_jsonl(output_json, results)
    status_counts = Counter(str(row["status"]) for row in results)
    recommendation_counts = Counter(str(row["recommendation"]) for row in results)
    lines = [
        "# Existing Content Audit", "", f"- Generated at: {utc_now()}", f"- Target locale: `{target_locale}`",
        f"- Items: {len(results)}", "", "## Summary", "",
        "| Status | Count |", "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in sorted(status_counts.items()))
    lines += ["", "| Recommendation | Count |", "|---|---:|"]
    lines.extend(f"| {key} | {value} |" for key, value in sorted(recommendation_counts.items()))
    lines += ["", "## Items", "", "| Item | Resource | Status | Score | Recommendation |", "|---|---|---|---:|---|"]
    for row in results:
        lines.append(f"| `{row['item_id']}` | {row['resource_type']} | {row['status']} | {row['trust_score']} | {row['recommendation']} |")
    lines += ["", "## Next action", "", "Run create_job.py for items that should be translated."]
    report_path = workspace / "reports" / "content-audit.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_history(workspace, "content_audited", {"target_locale": target_locale, "item_count": len(results)})
    print(json.dumps({"result": "ok", "items": len(results), "status": status_counts, "report": str(report_path)}))
    print("Next: python3 scripts/create_job.py --workspace " + str(workspace) + " --target-locale " + target_locale)


def missing_check(checks: list[dict[str, str]]) -> bool:
    return any(check["rule_id"] == "missing_translation" and check["status"] == "warning" for check in checks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit imported content using active guidelines.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--target-locale", required=True)
    parser.add_argument("--resource-type", nargs="*", help="Optional resource type filter.")
    parser.add_argument("--ids", help="Comma-separated catalog item IDs.")
    return parser


def main() -> int:
    try:
        audit(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
