from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import yaml


SUPPORTED_MODES = {"pure", "shopify_controlled"}
SUPPORTED_RESOURCE_TYPES = {
    "product_title", "product_description", "product_variant",
    "collection_title", "collection_description", "page_title", "page_body",
    "blog_title", "blog_body", "seo_title", "seo_description",
    "care_and_compliance",
}
ACTIONABLE_CATALOG_STATUSES = {"new", "changed", "unchanged", "pending"}
SOURCE_STRATEGIES = {"base", "bridge", "existing_translation"}
TRANSLATION_MODES = {"translation", "localization_only", "manual_only"}
AUDIT_POLICIES = {"standard", "seo_and_legal", "custom"}
AUDIT_MODES = {"auto", "paired_source", "target_only"}
JOB_ITEM_STATUSES = {
    "pending", "translated", "needs_review", "approved", "rejected",
    "blocked", "exported", "stale",
}


class BrandLabError(Exception):
    """User-facing error. Message must identify file, field, reason, and fix."""


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned[:48] or "item"


def stable_id(*parts: object) -> str:
    identity = "|".join(str(part) for part in parts)
    return "item_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise BrandLabError(f"{path}: missing required file. Create it with init_workspace.py.") from exc
    except json.JSONDecodeError as exc:
        raise BrandLabError(f"{path}: invalid JSON at line {exc.lineno}. Fix JSON syntax and retry.") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BrandLabError(f"{path}:{line_no}: invalid JSONL. Put one JSON object on each line.") from exc
            if not isinstance(value, dict):
                raise BrandLabError(f"{path}:{line_no}: each JSONL row must be an object.")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    atomic_write_text(path, content)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [{k: (v or "") for k, v in row.items()} for row in csv.DictReader(handle)]
    except FileNotFoundError as exc:
        raise BrandLabError(f"{path}: file not found. Put the source file in inbox/ or correct --file.") from exc
    except csv.Error as exc:
        raise BrandLabError(f"{path}: invalid CSV. Check quoting, encoding, and line breaks.") from exc


def resolve_workspace(workspace: Path) -> Path:
    path = workspace.expanduser().resolve()
    if not path.exists():
        raise BrandLabError(f"{path}: workspace not found. Run init_workspace.py first.")
    if not (path / "brand.yaml").is_file():
        raise BrandLabError(f"{path}: brand.yaml is missing. This is not a BrandLab workspace.")
    return path


def workspace_path(workspace: Path, *parts: str) -> Path:
    return workspace.joinpath(*parts)


def load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise BrandLabError(f"{path}: file not found. Initialize the workspace or correct the path.") from exc
    except yaml.YAMLError as exc:
        raise BrandLabError(f"{path}: invalid YAML. Fix the YAML syntax shown below: {exc}") from exc


def append_history(workspace: Path, event_type: str, payload: dict[str, Any]) -> None:
    history_path = workspace / "history" / "events.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": utc_now(), "actor": "brandlab-skill", "event_type": event_type, "payload": payload}
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def normalize_market_registry(brand: dict[str, Any], config_path: Path) -> dict[str, Any]:
    """Apply v0.3 market defaults while retaining compatibility with v0.2 files."""
    source = brand.get("source")
    if source is not None and not isinstance(source, dict):
        raise BrandLabError(f"{config_path}: source must be a mapping with default_locale.")
    if isinstance(source, dict) and not str(source.get("default_locale", "")).strip():
        raise BrandLabError(f"{config_path}: source.default_locale cannot be empty.")
    if not isinstance(brand.get("markets"), list):
        raise BrandLabError(f"{config_path}: markets must be a list.")

    seen_ids: set[str] = set()
    seen_locales: set[str] = set()
    normalized: list[dict[str, Any]] = []
    source_language = str(brand.get("source_language") or "")
    for index, raw in enumerate(brand["markets"]):
        label = f"markets[{index}]"
        if not isinstance(raw, dict) or not {"market", "locale", "enabled"}.issubset(raw):
            raise BrandLabError(f"{config_path}: {label} needs market, locale, and enabled.")
        market = str(raw["market"]).strip()
        locale = str(raw["locale"]).strip()
        if not market or not locale:
            raise BrandLabError(f"{config_path}: {label} market and locale cannot be empty.")
        market_id = str(raw.get("id") or market).strip()
        if market_id in seen_ids:
            raise BrandLabError(f"{config_path}: {label} id '{market_id}' is duplicated. Use a unique market id.")
        locale_key = locale.casefold()
        if locale_key in seen_locales:
            raise BrandLabError(f"{config_path}: {label} locale '{locale}' is duplicated. Markets must use unique BrandLab locales.")
        seen_ids.add(market_id)
        seen_locales.add(locale_key)

        shopify_locale = str(raw.get("shopify_locale") or locale_base(locale)).strip()
        source_strategy = str(raw.get("source_strategy") or "base")
        translation_mode = str(raw.get("translation_mode") or "translation")
        review_required = raw.get("review_required", True)
        audit_policy = str(raw.get("audit_policy") or "standard")
        audit_mode = str(raw.get("audit_mode") or "auto")
        if source_strategy not in SOURCE_STRATEGIES:
            raise BrandLabError(f"{config_path}: {label}.source_strategy must be one of {sorted(SOURCE_STRATEGIES)}.")
        if translation_mode not in TRANSLATION_MODES:
            raise BrandLabError(f"{config_path}: {label}.translation_mode must be one of {sorted(TRANSLATION_MODES)}.")
        if audit_policy not in AUDIT_POLICIES:
            raise BrandLabError(f"{config_path}: {label}.audit_policy must be one of {sorted(AUDIT_POLICIES)}.")
        if audit_mode not in AUDIT_MODES:
            raise BrandLabError(f"{config_path}: {label}.audit_mode must be one of {sorted(AUDIT_MODES)}.")
        if not isinstance(review_required, bool):
            raise BrandLabError(f"{config_path}: {label}.review_required must be true or false.")

        entry = dict(raw)
        entry.update({
            "id": market_id,
            "market": market,
            "locale": locale,
            "shopify_locale": shopify_locale,
            "enabled": raw["enabled"] is True,
            "source_strategy": source_strategy,
            "translation_mode": translation_mode,
            "review_required": review_required,
            "audit_policy": audit_policy,
            "audit_mode": audit_mode,
        })
        if translation_mode == "translation" and source_language and locale_base(locale) == locale_base(source_language):
            entry["translation_mode"] = "localization_only"
        normalized.append(entry)
    brand["markets"] = normalized
    return brand


def load_brand(workspace: Path) -> dict[str, Any]:
    config_path = workspace / "brand.yaml"
    brand = load_yaml(config_path)
    if not isinstance(brand, dict):
        raise BrandLabError(f"{config_path}: top level must be a mapping.")
    required = ("brand", "mode", "source_language", "working_language", "markets")
    missing = [field for field in required if field not in brand]
    if missing:
        raise BrandLabError(f"{config_path}: missing fields {missing}. Add them and retry.")
    if brand["mode"] not in SUPPORTED_MODES:
        raise BrandLabError(f"{config_path}: mode must be one of {sorted(SUPPORTED_MODES)}.")
    return normalize_market_registry(brand, config_path)


def find_market(brand: dict[str, Any], market_id: str) -> dict[str, Any]:
    wanted = market_id.strip().casefold()
    for market in brand.get("markets", []):
        if str(market.get("id", "")).casefold() == wanted or str(market.get("market", "")).casefold() == wanted:
            return market
    known = ", ".join(str(item.get("id") or item.get("market")) for item in brand.get("markets", []))
    raise BrandLabError(f"market '{market_id}': not found in brand.yaml. Configured markets are {known}.")


def locale_base(locale: str) -> str:
    return re.split(r"[-_]", locale.strip(), maxsplit=1)[0].lower()


def market_for_locale(brand: dict[str, Any], target_locale: str) -> str:
    for market in brand.get("markets", []):
        if market.get("locale", "").lower() == target_locale.lower():
            return str(market.get("market", ""))
    return locale_base(target_locale).upper()


def load_guidelines(workspace: Path) -> list[tuple[Path, dict[str, Any]]]:
    root = workspace / "guidelines"
    result: list[tuple[Path, dict[str, Any]]] = []
    if not root.exists():
        return result
    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        value = load_yaml(path)
        if not isinstance(value, dict):
            raise BrandLabError(f"{path}: guideline must be a YAML mapping.")
        required = {"id", "name", "resource_type", "target_locale", "status", "version", "rules"}
        missing = sorted(required - set(value))
        if missing:
            raise BrandLabError(f"{path}: guideline is missing {missing}. Add the fields and retry.")
        if not isinstance(value["rules"], list):
            raise BrandLabError(f"{path}: rules must be a list.")
        result.append((path, value))
    return result


def match_guidelines(
    guidelines: list[tuple[Path, dict[str, Any]]], item: dict[str, Any]
) -> list[tuple[Path, dict[str, Any]]]:
    matched: list[tuple[Path, dict[str, Any]]] = []
    for path, guideline in guidelines:
        if guideline.get("status") != "active":
            continue
        if guideline.get("resource_type") not in {"*", item.get("resource_type")}:
            continue
        target = str(guideline.get("target_locale", ""))
        market = str(guideline.get("market", ""))
        if target and target not in {"*", item.get("target_locale")}:
            continue
        if market and market not in {"*", item.get("market", "")}:
            continue
        matched.append((path, guideline))
    ids = [g["id"] for _, g in matched]
    duplicates = [value for value, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise BrandLabError(f"guidelines: duplicate active guideline IDs {duplicates}. Keep one version per ID.")
    return matched


def read_glossary(path: Path) -> list[dict[str, str]]:
    rows = read_csv_rows(path)
    required = {"source_term", "target_term", "target_locale", "status"}
    for line_no, row in enumerate(rows, 2):
        missing = sorted(required - set(row))
        if missing:
            raise BrandLabError(f"{path}:{line_no}: glossary row is missing {missing}.")
        if row["status"] not in {"locked", "preferred", "forbidden", "draft"}:
            raise BrandLabError(f"{path}:{line_no}: status must be locked, preferred, forbidden, or draft.")
    return rows


def glossary_for_locale(rows: list[dict[str, str]], target_locale: str) -> list[dict[str, str]]:
    return [row for row in rows if row["target_locale"].lower() == target_locale.lower()]


def glossary_digest(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return sha256_text(canonical)


def read_catalog(workspace: Path) -> list[dict[str, Any]]:
    return read_jsonl(workspace / "content" / "catalog.jsonl")


def write_catalog(workspace: Path, rows: list[dict[str, Any]]) -> None:
    write_jsonl(workspace / "content" / "catalog.jsonl", rows)


def load_job(workspace: Path, job_id: str) -> tuple[Path, dict[str, Any]]:
    job_dir = workspace / "jobs" / job_id
    manifest_path = job_dir / "manifest.json"
    if not manifest_path.is_file():
        raise BrandLabError(f"{manifest_path}: job not found. Run status.py to list valid jobs.")
    value = read_json(manifest_path)
    if not isinstance(value, dict) or value.get("job_id") != job_id:
        raise BrandLabError(f"{manifest_path}: manifest job_id does not match its folder name.")
    return job_dir, value


def save_job(job_dir: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    atomic_write_json(job_dir / "manifest.json", manifest)


def load_guideline_snapshots(job_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for guideline_ref in manifest.get("guidelines", []):
        filename = guideline_ref.get("snapshot")
        path = job_dir / filename if filename else job_dir / guideline_ref.get("path", "")
        value = load_yaml(path)
        if isinstance(value, dict):
            snapshots.append(value)
    return snapshots


def load_glossary_snapshot(job_dir: Path) -> list[dict[str, str]]:
    return read_glossary(job_dir / "context" / "glossary.csv")


def normalize_term(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)).casefold().strip()


PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}|\{[^{}]*\}|%[sdif]")


class TagCollector(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in self.VOID_TAGS:
            self.tags.append(tag.lower())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in self.VOID_TAGS:
            self.tags.append(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in self.VOID_TAGS:
            self.tags.append(tag.lower())


def extract_tags(value: str) -> list[str]:
    if "<" not in value or ">" not in value:
        return []
    collector = TagCollector()
    try:
        collector.feed(value)
    except Exception:
        return []
    return collector.tags


def rule_values(guidelines: list[dict[str, Any]], rule_type: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for guideline in guidelines:
        for rule in guideline.get("rules", []):
            if isinstance(rule, dict) and rule.get("type") == rule_type:
                result.append((guideline, rule))
    return result


def calculate_trust_score(checks: list[dict[str, Any]]) -> int:
    hard_failures = [check for check in checks if check["status"] == "failed" and check["severity"] == "hard"]
    warnings = [check for check in checks if check["status"] == "warning"]
    if hard_failures:
        return max(0, 60 - 5 * (len(hard_failures) - 1))
    return max(70, 100 - 2 * len(warnings)) if warnings else 100


def make_check(rule_id: str, status: str, severity: str, message: str = "") -> dict[str, str]:
    return {"rule_id": rule_id, "status": status, "severity": severity, "message": message}


def validate_translation(
    source: str,
    translated: str,
    source_digest: str,
    expected_digest: str,
    source_locale: str,
    target_locale: str,
    expected_source_locale: str,
    expected_target_locale: str,
    guidelines: list[dict[str, Any]],
    glossary: list[dict[str, str]],
    other_translations: set[str] | None = None,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    other_translations = other_translations or set()

    checks.append(make_check(
        "source_digest_consistency", "passed" if source_digest == expected_digest else "failed", "hard",
        "Translation source digest matches the job snapshot." if source_digest == expected_digest else
        f"Expected {expected_digest}, received {source_digest}. Regenerate from the current source snapshot."
    ))
    checks.append(make_check(
        "target_locale_consistency",
        "passed" if target_locale.lower() == expected_target_locale.lower() else "failed", "hard",
        "Target locale matches the job." if target_locale.lower() == expected_target_locale.lower() else
        f"Expected {expected_target_locale}, received {target_locale}."
    ))
    if expected_source_locale and source_locale and source_locale.lower() != expected_source_locale.lower():
        checks.append(make_check(
            "source_locale_consistency", "failed", "hard",
            f"Expected source locale {expected_source_locale}, received {source_locale}."
        ))
    else:
        checks.append(make_check("source_locale_consistency", "passed", "hard"))

    if not translated.strip():
        checks.append(make_check("empty_translation", "failed", "hard", "Translation is empty. Provide non-empty text."))
    else:
        checks.append(make_check("empty_translation", "passed", "hard"))

    normalized_source = re.sub(r"\s+", " ", source).strip().casefold()
    normalized_output = re.sub(r"\s+", " ", translated).strip().casefold()
    if normalized_source and normalized_source == normalized_output:
        checks.append(make_check("untranslated_source", "warning", "soft", "Output is identical to the source after normalization."))
    elif SequenceMatcher(None, normalized_source, normalized_output).ratio() >= 0.98 and len(normalized_source) > 24:
        checks.append(make_check("untranslated_source", "warning", "soft", "Output is nearly identical to the source."))
    else:
        checks.append(make_check("untranslated_source", "passed", "soft"))

    source_placeholders = sorted(PLACEHOLDER_RE.findall(source))
    output_placeholders = sorted(PLACEHOLDER_RE.findall(translated))
    if source_placeholders == output_placeholders:
        checks.append(make_check("placeholder_parity", "passed", "hard"))
    else:
        checks.append(make_check(
            "placeholder_parity", "failed", "hard",
            f"Expected placeholders {source_placeholders}, received {output_placeholders}. Preserve every placeholder exactly."
        ))

    source_tags = Counter(extract_tags(source))
    output_tags = Counter(extract_tags(translated))
    if source_tags == output_tags:
        checks.append(make_check("html_tag_parity", "passed", "hard"))
    else:
        checks.append(make_check(
            "html_tag_parity", "failed", "hard",
            f"Expected HTML tag multiset {dict(source_tags)}, received {dict(output_tags)}. Preserve all source tags."
        ))

    matched_glossary = [
        row for row in glossary
        if row["target_locale"].lower() == target_locale.lower() or locale_base(row["target_locale"]) == locale_base(target_locale)
    ]
    for row in matched_glossary:
        source_hit = normalize_term(row["source_term"]) in normalize_term(source)
        target_hit = normalize_term(row["target_term"]) in normalize_term(translated)
        if row["status"] == "locked":
            if source_hit and not target_hit:
                checks.append(make_check(
                    "locked_glossary_term", "failed", "hard",
                    f"Locked term '{row['source_term']}' must be translated as '{row['target_term']}'."
                ))
            else:
                checks.append(make_check("locked_glossary_term", "passed", "hard"))
        elif row["status"] == "preferred":
            if source_hit and not target_hit:
                checks.append(make_check(
                    "preferred_glossary_term", "warning", "soft",
                    f"Preferred term '{row['target_term']}' is missing."
                ))
            else:
                checks.append(make_check("preferred_glossary_term", "passed", "soft"))
        elif row["status"] == "forbidden" and target_hit:
            checks.append(make_check(
                "forbidden_glossary_term", "failed", "hard",
                f"Forbidden term '{row['target_term']}' appears in the translation."
            ))
        else:
            checks.append(make_check("glossary_term", "passed", "hard"))

    for guideline, rule in rule_values(guidelines, "forbidden_term"):
        value = str(rule.get("value", ""))
        if value and normalize_term(value) in normalize_term(translated):
            checks.append(make_check(
                f"forbidden_term:{guideline['id']}", "failed", "hard",
                f"Forbidden term '{value}' appears in the translation."
            ))
        elif value:
            checks.append(make_check(f"forbidden_term:{guideline['id']}", "passed", "hard"))

    for guideline, rule in rule_values(guidelines, "forbidden_regex"):
        pattern = str(rule.get("pattern", ""))
        try:
            hit = re.search(pattern, translated, flags=re.IGNORECASE if rule.get("ignore_case", True) else 0)
        except re.error as exc:
            raise BrandLabError(f"guideline {guideline['id']}: invalid forbidden_regex pattern. Fix {pattern}: {exc}") from exc
        if hit:
            checks.append(make_check(
                f"forbidden_regex:{guideline['id']}", "failed", "hard",
                f"Forbidden pattern '{pattern}' matched '{hit.group(0)}'."
            ))
        else:
            checks.append(make_check(f"forbidden_regex:{guideline['id']}", "passed", "hard"))

    for guideline, rule in rule_values(guidelines, "required_regex"):
        pattern = str(rule.get("pattern", ""))
        try:
            hit = re.search(pattern, translated, flags=re.IGNORECASE if rule.get("ignore_case", True) else 0)
        except re.error as exc:
            raise BrandLabError(f"guideline {guideline['id']}: invalid required_regex pattern. Fix {pattern}: {exc}") from exc
        if hit:
            checks.append(make_check(f"required_regex:{guideline['id']}", "passed", "hard"))
        else:
            checks.append(make_check(
                f"required_regex:{guideline['id']}", "failed", "hard",
                f"Required pattern '{pattern}' is missing."
            ))

    for guideline, rule in rule_values(guidelines, "legal_claim"):
        source_pattern = str(rule.get("source_regex", ""))
        required_pattern = str(rule.get("required_regex", ""))
        try:
            source_hit = bool(re.search(source_pattern, source, flags=re.I))
            target_hit = bool(re.search(required_pattern, translated, flags=re.I))
        except re.error as exc:
            raise BrandLabError(f"guideline {guideline['id']}: invalid legal_claim regex. Fix patterns: {exc}") from exc
        if source_hit and not target_hit:
            checks.append(make_check(
                f"legal_claim:{guideline['id']}", "failed", "hard",
                "Source contains a regulated claim; the required target wording is missing."
            ))
        else:
            checks.append(make_check(f"legal_claim:{guideline['id']}", "passed", "hard"))

    lengths = [(guideline, rule) for guideline, rule in rule_values(guidelines, "max_length") if isinstance(rule.get("value"), int)]
    if lengths:
        limit = min(rule["value"] for _, rule in lengths)
        if len(translated) <= limit:
            checks.append(make_check("max_length", "passed", "hard"))
        else:
            checks.append(make_check(
                "max_length", "failed", "hard",
                f"Translation is {len(translated)} characters; maximum is {limit}."
            ))
    min_rules = [(guideline, rule) for guideline, rule in rule_values(guidelines, "min_length") if isinstance(rule.get("value"), int)]
    if min_rules:
        minimum = max(rule["value"] for _, rule in min_rules)
        if len(translated.strip()) >= minimum:
            checks.append(make_check("min_length", "passed", "hard"))
        else:
            checks.append(make_check(
                "min_length", "failed", "hard",
                f"Translation is {len(translated.strip())} characters; minimum is {minimum}."
            ))

    if other_translations and normalized_output in {normalize_term(value) for value in other_translations}:
        checks.append(make_check("duplicate_output", "warning", "soft", "Output is identical to another item in this job."))
    else:
        checks.append(make_check("duplicate_output", "passed", "soft"))

    for guideline, rule in rule_values(guidelines, "preserve"):
        value = str(rule.get("value", "")).casefold()
        if value == "placeholders" and source_placeholders == output_placeholders:
            checks.append(make_check(f"preserve_placeholders:{guideline['id']}", "passed", "soft"))
        elif value == "placeholders":
            checks.append(make_check(f"preserve_placeholders:{guideline['id']}", "warning", "soft", "Placeholder parity failed; see placeholder_parity."))
        elif value == "html_tags" and source_tags == output_tags:
            checks.append(make_check(f"preserve_html_tags:{guideline['id']}", "passed", "soft"))
        elif value == "html_tags":
            checks.append(make_check(f"preserve_html_tags:{guideline['id']}", "warning", "soft", "HTML parity failed; see html_tag_parity."))
    return checks


def item_status_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(check["status"] == "failed" and check["severity"] == "hard" for check in checks):
        return "blocked"
    if any(check["status"] == "warning" for check in checks):
        return "needs_review"
    return "translated"


def render_audit_summary(checks: list[dict[str, Any]]) -> str:
    failed = sum(check["status"] == "failed" for check in checks)
    warnings = sum(check["status"] == "warning" for check in checks)
    return f"{failed} failed, {warnings} warnings"
