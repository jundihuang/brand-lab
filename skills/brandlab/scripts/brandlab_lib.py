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
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import yaml

from validation_lib import (
    SchemaValidationError,
    resolve_inside as resolve_inside_unsafe,
    validate_row_schema as validate_row_schema_unsafe,
    validate_schema as validate_schema_unsafe,
)


SUPPORTED_MODES = {"pure", "shopify_controlled"}
SUPPORTED_RESOURCE_TYPES = {
    "product_title", "product_description", "product_variant",
    "collection_title", "collection_description", "page_title", "page_body",
    "blog_title", "blog_body", "seo_title", "seo_description",
    "care_and_compliance",
}
ACTIONABLE_CATALOG_STATUSES = {"new", "changed", "unchanged", "pending"}
DEFAULT_CSV_IMPORT_MAPPINGS = {
    "title": "product_title",
    "product title": "product_title",
    "body html": "product_description",
    "body": "product_description",
    "seo title": "seo_title",
    "seo description": "seo_description",
    "collection title": "collection_title",
    "collection description": "collection_description",
    "page title": "page_title",
    "page body": "page_body",
    "blog title": "blog_title",
    "blog body": "blog_body",
}
GENERIC_CSV_SOURCE_COLUMNS = {"source", "translation", "translation source", "translated text"}
DEFAULT_SHOPIFY_EXPORT_FIELD_MAPPINGS = {
    "product_title": "Title",
    "product_description": "Body (HTML)",
    "seo_title": "SEO Title",
    "seo_description": "SEO Description",
}
SHOPIFY_REQUIRED_EXPORT_FIELDS = {"Handle", "Target Locale", "BrandLab Item IDs", "Source Digests"}
SOURCE_STRATEGIES = {"base", "bridge", "existing_translation"}
TRANSLATION_MODES = {"translation", "localization_only", "manual_only"}
AUDIT_POLICIES = {"standard", "seo_and_legal", "custom"}
AUDIT_MODES = {"auto", "paired_source", "target_only"}
JOB_ITEM_STATUSES = {
    "pending", "translated", "needs_review", "approved", "rejected",
    "blocked", "exported", "stale", "verified", "mismatched",
}


class BrandLabError(Exception):
    """User-facing error. Message must identify file, field, reason, and fix."""


def validate_schema(name: str, value: Any, path: Path | str) -> None:
    try:
        validate_schema_unsafe(name, value, path)
    except SchemaValidationError as exc:
        raise BrandLabError(str(exc)) from exc


def validate_row_schema(name: str, row: Any, path: Path | str, line_no: int) -> None:
    try:
        validate_row_schema_unsafe(name, row, path, line_no)
    except SchemaValidationError as exc:
        raise BrandLabError(str(exc)) from exc


def resolve_inside(root: Path, relative: Path | str) -> Path:
    try:
        return resolve_inside_unsafe(root, relative)
    except SchemaValidationError as exc:
        raise BrandLabError(str(exc)) from exc


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


def read_json_contract(path: Path, schema_name: str) -> Any:
    value = read_json(path)
    validate_schema(schema_name, value, path)
    return value


def write_json_contract(path: Path, value: Any, schema_name: str) -> None:
    validate_schema(schema_name, value, path)
    atomic_write_json(path, value)


def read_jsonl_contract(path: Path, schema_name: str) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    for line_no, row in enumerate(rows, 1):
        validate_row_schema(schema_name, row, path, line_no)
    return rows


def write_jsonl_contract(path: Path, rows: Iterable[dict[str, Any]], schema_name: str) -> None:
    for line_no, row in enumerate(rows, 1):
        validate_row_schema(schema_name, row, path, line_no)
    write_jsonl(path, rows)


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
    try:
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
    except UnicodeError as exc:
        raise BrandLabError(f"{path}: file is not valid UTF-8. Re-export it as UTF-8 or UTF-8 with BOM.") from exc
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    atomic_write_text(path, content)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            headers = [str(value) for value in reader.fieldnames]
            duplicates = sorted({value for value in headers if headers.count(value) > 1})
            if duplicates:
                raise BrandLabError(
                    f"{path}: duplicate CSV headers {duplicates}. Rename each column so mappings are unambiguous."
                )
            return [{k: (v or "") for k, v in row.items()} for row in reader]
    except FileNotFoundError as exc:
        raise BrandLabError(f"{path}: file not found. Put the source file in inbox/ or correct --file.") from exc
    except UnicodeError as exc:
        raise BrandLabError(f"{path}: file is not valid UTF-8. Re-export it as UTF-8 or UTF-8 with BOM.") from exc
    except csv.Error as exc:
        raise BrandLabError(f"{path}: invalid CSV at record {exc.lineno}. Check quoting, encoding, and line breaks.") from exc


def read_text_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise BrandLabError(f"{path}: file not found. Put the source file in inbox/ or correct --file.") from exc
    except UnicodeError as exc:
        raise BrandLabError(f"{path}: file is not valid UTF-8. Re-export it as UTF-8 or UTF-8 with BOM.") from exc


def resolve_workspace(workspace: Path) -> Path:
    path = workspace.expanduser().resolve()
    if not path.exists():
        raise BrandLabError(f"{path}: workspace not found. Run init_workspace.py first.")
    if not (path / "brand.yaml").is_file():
        raise BrandLabError(f"{path}: brand.yaml is missing. This is not a BrandLab workspace.")
    return path


def workspace_path(workspace: Path, *parts: str) -> Path:
    return workspace.joinpath(*parts)


def resolve_job_path(job_dir: Path, relative: Path | str) -> Path:
    return resolve_inside(job_dir, relative)


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
    validate_schema("history-event", event, history_path)
    rows = read_jsonl(history_path) + [event]
    write_jsonl(history_path, rows)


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
            "bridge_locale": str(raw.get("bridge_locale") or "") or None,
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
    validate_schema("brand", brand, config_path)
    required = ("brand", "mode", "source_language", "working_language", "markets")
    missing = [field for field in required if field not in brand]
    if missing:
        raise BrandLabError(f"{config_path}: missing fields {missing}. Add them and retry.")
    if brand["mode"] not in SUPPORTED_MODES:
        raise BrandLabError(f"{config_path}: mode must be one of {sorted(SUPPORTED_MODES)}.")
    normalized = normalize_market_registry(brand, config_path)
    validate_schema("brand", normalized, config_path)
    return normalized


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


def lint_guideline(value: dict[str, Any], path: Path | str = "guideline") -> list[str]:
    """Return deterministic, actionable lint errors for one guideline."""
    errors: list[str] = []
    guideline_id = str(value.get("id", ""))
    locale = str(value.get("target_locale", ""))
    market = str(value.get("market", ""))
    if not re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", guideline_id):
        errors.append(f"{path}: id: use letters, numbers, hyphen, or underscore. Fix: rename {guideline_id!r}.")
    if not str(value.get("name", "")).strip():
        errors.append(f"{path}: name: cannot be empty. Fix: add a human-readable name.")
    if not re.match(r"^[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?$", locale):
        errors.append(f"{path}: target_locale: use BCP-47-style locale. Fix: {locale!r}.")
    if market and market != "*" and not re.match(r"^[A-Z]{2,3}$", market):
        errors.append(f"{path}: market: use an uppercase ISO-style market ID or '*'. Fix: {market!r}.")
    if value.get("resource_type") != "*" and str(value.get("resource_type", "")) not in SUPPORTED_RESOURCE_TYPES:
        errors.append(f"{path}: resource_type: unsupported {value.get('resource_type')!r}. Fix: use a supported Shopify resource type or '*'.")

    rules = value.get("rules", [])
    if not isinstance(rules, list):
        errors.append(f"{path}: rules: must be a list. Fix: convert rules to a YAML list.")
        return errors

    lengths: list[int] = []
    forbidden: set[str] = set()
    required: set[str] = set()
    for index, rule in enumerate(rules):
        label = f"{path}:rules[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{label}: rule must be a mapping. Fix: use type/value fields.")
            continue
        rule_type = str(rule.get("type", ""))
        if rule_type not in {
            "instruction", "max_length", "min_length", "forbidden_term", "required_term",
            "forbidden_regex", "required_regex", "legal_claim", "preserve",
            "html_allowed_tags", "html_allowed_attributes",
        }:
            errors.append(f"{label}: type: unsupported rule type {rule_type!r}. Fix: use a supported rule type.")
        if rule_type == "max_length":
            if isinstance(rule.get("value"), int) and not isinstance(rule.get("value"), bool):
                lengths.append(int(rule["value"]))
            else:
                errors.append(f"{label}: value: max_length requires an integer. Fix: set a character limit.")
        if rule_type == "min_length" and (not isinstance(rule.get("value"), int) or isinstance(rule.get("value"), bool)):
            errors.append(f"{label}: value: min_length requires an integer. Fix: set a character limit.")
        if rule_type in {"forbidden_term", "required_term"}:
            term = normalize_term(str(rule.get("value", "")))
            if not term:
                errors.append(f"{label}: value: {rule_type} cannot be empty.")
            elif rule_type == "forbidden_term":
                forbidden.add(term)
            else:
                required.add(term)
        if rule_type == "forbidden_regex":
            try:
                re.compile(str(rule.get("pattern", "")))
            except re.error as exc:
                errors.append(f"{label}: pattern: invalid regex. Fix: {exc}.")
        if rule_type == "required_regex":
            try:
                re.compile(str(rule.get("pattern", "")))
            except re.error as exc:
                errors.append(f"{label}: pattern: invalid regex. Fix: {exc}.")
        if rule_type == "legal_claim":
            for field_name in ("source_regex", "required_regex"):
                try:
                    re.compile(str(rule.get(field_name, "")))
                except re.error as exc:
                    errors.append(f"{label}: {field_name}: invalid regex. Fix: {exc}.")

    if lengths and min(lengths) < 0:
        errors.append(f"{path}: rules: max_length cannot be negative.")
    minimums = [int(rule.get("value")) for rule in rules if isinstance(rule, dict) and rule.get("type") == "min_length" and isinstance(rule.get("value"), int)]
    if lengths and minimums and max(minimums) > min(lengths):
        errors.append(f"{path}: rules: min_length {max(minimums)} exceeds max_length {min(lengths)}. Fix: make ranges compatible.")
    contradiction = sorted(forbidden & required)
    if contradiction:
        errors.append(f"{path}: rules: terms {contradiction} are both forbidden and required. Fix: resolve the contradiction.")
    return errors


def lint_guideline_cross_file(guidelines: list[tuple[Path, dict[str, Any]]]) -> None:
    active_ids: dict[str, Path] = {}
    duplicates: list[str] = []
    for path, guideline in guidelines:
        if guideline.get("status") != "active":
            continue
        guideline_id = str(guideline.get("id", ""))
        if guideline_id in active_ids:
            duplicates.append(guideline_id)
        else:
            active_ids[guideline_id] = path
    if duplicates:
        files = ", ".join(str(active_ids[value]) for value in duplicates)
        raise BrandLabError(f"guidelines: duplicate active guideline IDs {duplicates} in {files}. Keep one active version per ID.")


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
        validate_schema("guideline", value, path)
        result.append((path, value))
        lint_errors = lint_guideline(value, path)
        if lint_errors:
            raise BrandLabError("; ".join(lint_errors))
    lint_guideline_cross_file(result)
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
        validate_row_schema("glossary", row, path, line_no)
    return rows


def glossary_for_locale(rows: list[dict[str, str]], target_locale: str) -> list[dict[str, str]]:
    return [row for row in rows if row["target_locale"].lower() == target_locale.lower()]


def glossary_digest(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return sha256_text(canonical)


def read_catalog(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "content" / "catalog.jsonl"
    rows = read_jsonl(path)
    for line_no, row in enumerate(rows, 1):
        try:
            validate_schema("content-item", row, f"{path}:{line_no}")
        except BrandLabError as exc:
            raise BrandLabError(str(exc).replace("SCHEMA_INVALID", f"SCHEMA_INVALID line {line_no}")) from exc
    return rows


def write_catalog(workspace: Path, rows: list[dict[str, Any]]) -> None:
    path = workspace / "content" / "catalog.jsonl"
    for line_no, row in enumerate(rows, 1):
        validate_schema("content-item", row, f"{path}:{line_no}")
    write_jsonl(path, rows)


def load_job(workspace: Path, job_id: str) -> tuple[Path, dict[str, Any]]:
    jobs_root = workspace / "jobs"
    safe_job_dir = resolve_inside(jobs_root, job_id)
    if safe_job_dir.name != Path(job_id).name:
        raise BrandLabError(f"job '{job_id}': invalid job id. Use a single folder name without path separators.")
    manifest_path = safe_job_dir / "manifest.json"
    if not manifest_path.is_file():
        raise BrandLabError(f"{manifest_path}: job not found. Run status.py to list valid jobs.")
    value = read_json(manifest_path)
    if not isinstance(value, dict) or value.get("job_id") != Path(job_id).name:
        raise BrandLabError(f"{manifest_path}: manifest job_id does not match its folder name.")
    validate_schema("job", value, manifest_path)
    return safe_job_dir, value


def save_job(job_dir: Path, manifest: dict[str, Any]) -> None:
    manifest_path = job_dir / "manifest.json"
    manifest["updated_at"] = utc_now()
    validate_schema("job", manifest, manifest_path)
    atomic_write_json(manifest_path, manifest)


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


def normalized_csv_header(value: str) -> str:
    return re.sub(r"[\s_()\-]+", " ", value.strip()).casefold().strip()


def load_field_mappings(path: Path, explicit: bool = False) -> dict[str, dict[str, str]]:
    """Load optional CSV mapping overrides and merge them with deterministic defaults."""
    if not path.exists():
        if explicit:
            raise BrandLabError(f"{path}: field mapping file not found. Create it or omit --field-mapping.")
        return {
            "import_csv": dict(DEFAULT_CSV_IMPORT_MAPPINGS),
            "export_shopify": dict(DEFAULT_SHOPIFY_EXPORT_FIELD_MAPPINGS),
        }
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise BrandLabError(f"{path}: field mappings must be a YAML mapping.")
    validate_schema("field-mapping", value, path)

    import_mappings = dict(DEFAULT_CSV_IMPORT_MAPPINGS)
    for raw_column, resource_type in value.get("import_csv", {}).items():
        column = normalized_csv_header(str(raw_column))
        if not column:
            raise BrandLabError(f"{path}: import_csv contains an empty column name.")
        if column in import_mappings and import_mappings[column] != resource_type:
            raise BrandLabError(
                f"{path}: import_csv['{raw_column}'] conflicts with default mapping "
                f"'{import_mappings[column]}'. Use one resource type per column."
            )
        import_mappings[column] = str(resource_type)
    export_mappings = dict(DEFAULT_SHOPIFY_EXPORT_FIELD_MAPPINGS)
    for resource_type, raw_field in value.get("export_shopify", {}).items():
        if resource_type not in export_mappings:
            raise BrandLabError(
                f"{path}: export_shopify['{resource_type}'] is unsupported. "
                f"Use one of {sorted(export_mappings)}."
            )
        field = str(raw_field).strip()
        if not field:
            raise BrandLabError(f"{path}: export_shopify['{resource_type}'] cannot be empty.")
        if field in SHOPIFY_REQUIRED_EXPORT_FIELDS:
            raise BrandLabError(f"{path}: export_shopify['{resource_type}'] uses reserved field '{field}'. Choose a different field name.")
        export_mappings[str(resource_type)] = field
    duplicate_fields = sorted({
        field for field in export_mappings.values()
        if sum(1 for mapped in export_mappings.values() if mapped == field) > 1
    })
    if duplicate_fields:
        raise BrandLabError(f"{path}: export_shopify maps multiple resources to fields {duplicate_fields}. Keep one field per resource.")
    return {"import_csv": import_mappings, "export_shopify": export_mappings}


LANGUAGE_MARKERS = {
    "en": {"the", "and", "with", "from", "made", "for", "of"},
    "it": {"il", "lo", "la", "con", "di", "per", "una", "realizzata"},
    "fr": {"le", "la", "les", "avec", "pour", "une", "fabriquée"},
    "es": {"el", "la", "los", "con", "para", "una", "hecha"},
    "de": {"der", "die", "das", "mit", "für", "aus", "gefertigt"},
    "pt": {"o", "a", "os", "as", "com", "para", "uma", "feita"},
    "nl": {"de", "het", "een", "met", "voor", "van", "gemaakt"},
}
NON_LATIN_SCRIPTS = {
    "ja": r"[\u3040-\u30ff\u4e00-\u9fff]",
    "zh": r"[\u4e00-\u9fff]",
    "ko": r"[\uac00-\ud7af]",
    "ru": r"[\u0400-\u04ff]",
}
SHORT_TEXT_RESOURCES = {"product_title", "collection_title", "page_title", "blog_title", "seo_title", "seo_description", "product_variant"}
PLACEHOLDER_RE = re.compile(
    r"(?P<liquid>\{\{\s*[^{}]+\s*\}\})|(?P<icu>\{[^{},]+(?:\s*,[^{}]*)?\})|"
    r"(?P<printf>%(?:\d+\$)?[*0-9.\-]*[sdif])|(?P<format>%<[^<>]+>[sdif])"
)
PRINTF_PLACEHOLDER_RE = re.compile(r"%(?:\d+\$)?[*0-9.\-]*[sdif]|%<[^<>]+>[sdif]")


def _balanced_icu_end(value: str, start: int) -> int | None:
    """Return the end of an ICU-style balanced brace token, including simple arguments."""
    depth = 0
    quote = ""
    index = start
    while index < len(value):
        char = value[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def extract_placeholders(value: str) -> list[dict[str, str]]:
    """Extract deterministic placeholder tokens without changing their original text."""
    placeholders: list[dict[str, str]] = []
    index = 0
    while index < len(value):
        if value.startswith("{{", index):
            end = value.find("}}", index + 2)
            if end >= 0:
                end += 2
                placeholders.append({"type": "liquid", "token": value[index:end]})
                index = end
                continue
        if value.startswith("{", index):
            end = _balanced_icu_end(value, index)
            if end is not None:
                placeholders.append({"type": "icu", "token": value[index:end]})
                index = end
                continue
        if value[index] == "%":
            match = PRINTF_PLACEHOLDER_RE.match(value, index)
            if match:
                placeholder_type = "format" if match.group(0).startswith("%<") else "printf"
                placeholders.append({"type": placeholder_type, "token": match.group(0)})
                index = match.end()
                continue
        index += 1
    return placeholders


def language_suspicion(
    translated: str,
    target_locale: str,
    source_locale: str = "en",
    resource_type: str = "page_body",
) -> tuple[bool, bool]:
    """Return target-language-mismatch and untranslated-source suspicion."""
    words = set(re.findall(r"[a-z]+", translated.casefold()))
    target = locale_base(target_locale)
    source = locale_base(source_locale or "en")
    if target in NON_LATIN_SCRIPTS and re.search(NON_LATIN_SCRIPTS[target], translated):
        return False, False
    target_markers = LANGUAGE_MARKERS.get(target, set())
    source_markers = LANGUAGE_MARKERS.get(source, set())
    if source == target or not source_markers:
        return False, False
    source_hits = len(words & source_markers)
    if target not in LANGUAGE_MARKERS:
        return False, source_hits >= 1
    threshold = 1 if resource_type in SHORT_TEXT_RESOURCES else 2
    mismatch = len(words & target_markers) == 0 and source_hits >= threshold
    untranslated = source_hits >= threshold
    return mismatch, untranslated


@dataclass
class HtmlInspection:
    tags: Counter = field(default_factory=Counter)
    attributes: Counter = field(default_factory=Counter)
    unclosed_tags: list[str] = field(default_factory=list)
    unexpected_end_tags: list[str] = field(default_factory=list)
    mismatched_end_tags: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    event_handlers: list[str] = field(default_factory=list)
    unsafe_urls: list[str] = field(default_factory=list)

    @property
    def is_balanced(self) -> bool:
        return not self.unclosed_tags and not self.unexpected_end_tags and not self.mismatched_end_tags


class TagCollector(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
    BLOCKED_TAGS = {"script", "iframe", "object", "embed"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[str] = []
        self.stack: list[str] = []
        self.unclosed_tags: list[str] = []
        self.unexpected_end_tags: list[str] = []
        self.mismatched_end_tags: list[str] = []
        self.scripts: list[str] = []
        self.event_handlers: list[str] = []
        self.unsafe_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]], self_closing: bool = False) -> None:
        name = tag.lower()
        self.tags.append(name)
        for attr_name, attr_value in attrs:
            attr = attr_name.lower()
            self.attributes.append(attr)
            if attr.startswith("on"):
                self.event_handlers.append(f"{name}@{attr}")
            if attr in {"href", "src", "action"} and isinstance(attr_value, str) and re.match(
                r"^\s*(javascript|vbscript|data\s*:\s*text/html)", attr_value, flags=re.I
            ):
                self.unsafe_urls.append(f"{name}@{attr}")
        if name in self.BLOCKED_TAGS:
            self.scripts.append(name)
        if name not in self.VOID_TAGS and not self_closing:
            self.stack.append(name)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        self.tags.append(name)
        if name in self.VOID_TAGS:
            return
        if not self.stack:
            self.unexpected_end_tags.append(name)
            return
        if self.stack[-1] == name:
            self.stack.pop()
            return
        if name in self.stack:
            while self.stack and self.stack[-1] != name:
                self.mismatched_end_tags.append(self.stack.pop())
            if self.stack:
                self.stack.pop()
        else:
            self.unexpected_end_tags.append(name)

    def close(self) -> None:
        super().close()
        self.unclosed_tags = list(self.stack)


def inspect_html(value: str) -> HtmlInspection:
    inspection = HtmlInspection()
    if "<" not in value or ">" not in value:
        return inspection
    collector = TagCollector()
    collector.feed(value)
    collector.close()
    inspection.tags = Counter(collector.tags)
    inspection.attributes = Counter(collector.attributes)
    inspection.unclosed_tags = collector.unclosed_tags
    inspection.unexpected_end_tags = collector.unexpected_end_tags
    inspection.mismatched_end_tags = collector.mismatched_end_tags
    inspection.scripts = collector.scripts
    inspection.event_handlers = collector.event_handlers
    inspection.unsafe_urls = collector.unsafe_urls
    return inspection


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
    resource_type: str = "page_body",
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
    locked_values = {
        normalize_term(row["target_term"])
        for row in glossary
        if row["status"] == "locked" and (
            row.get("target_locale", "").lower() == target_locale.lower()
            or locale_base(row.get("target_locale", "")) == locale_base(target_locale)
        )
    }
    comparable_source = " ".join(part for part in re.split(r"\s+", normalized_source) if part not in locked_values)
    comparable_output = " ".join(part for part in re.split(r"\s+", normalized_output) if part not in locked_values)
    if normalized_source and normalized_source == normalized_output and locked_values and not comparable_source:
        checks.append(make_check("untranslated_source", "passed", "soft", "Exact match consists only of locked terms."))
    elif normalized_source and normalized_source == normalized_output:
        checks.append(make_check("untranslated_source", "warning", "soft", "Output is identical to the source after normalization."))
    elif comparable_source and comparable_source == comparable_output:
        checks.append(make_check("untranslated_source", "warning", "soft", "Output is identical to the source after removing locked terms."))
    elif SequenceMatcher(None, comparable_source, comparable_output).ratio() >= 0.98 and len(comparable_source) > 24:
        checks.append(make_check("untranslated_source", "warning", "soft", "Output is nearly identical to the source."))
    else:
        checks.append(make_check("untranslated_source", "passed", "soft"))

    language_mismatch, language_untranslated = language_suspicion(
        translated, target_locale, source_locale or expected_source_locale, resource_type
    )
    if language_mismatch:
        checks.append(make_check(
            "language_mismatch", "warning", "soft",
            f"Output does not contain expected {expected_target_locale} language markers."
        ))
    else:
        checks.append(make_check("language_mismatch", "passed", "soft"))

    source_placeholders = extract_placeholders(source)
    output_placeholders = extract_placeholders(translated)
    if source_placeholders == output_placeholders:
        checks.append(make_check("placeholder_parity", "passed", "hard"))
    else:
        expected_tokens = [item["token"] for item in source_placeholders]
        received_tokens = [item["token"] for item in output_placeholders]
        checks.append(make_check(
            "placeholder_parity", "failed", "hard",
            f"Expected placeholders {expected_tokens}, received {received_tokens}. Preserve every placeholder type and token exactly."
        ))

    source_html = inspect_html(source)
    output_html = inspect_html(translated)
    if source_html.tags == output_html.tags and output_html.is_balanced:
        checks.append(make_check("html_tag_parity", "passed", "hard"))
    else:
        problems = []
        if source_html.tags != output_html.tags:
            problems.append(f"expected tags {dict(source_html.tags)}, received {dict(output_html.tags)}")
        if output_html.unclosed_tags:
            problems.append(f"unclosed {output_html.unclosed_tags}")
        if output_html.unexpected_end_tags:
            problems.append(f"unexpected end tags {output_html.unexpected_end_tags}")
        if output_html.mismatched_end_tags:
            problems.append(f"mismatched nesting {output_html.mismatched_end_tags}")
        checks.append(make_check(
            "html_tag_parity", "failed", "hard",
            "; ".join(problems) + ". Preserve all source tags and valid HTML nesting."
        ))

    if output_html.scripts or output_html.event_handlers or output_html.unsafe_urls:
        checks.append(make_check(
            "html_security", "failed", "hard",
            f"Unsafe HTML found: scripts={output_html.scripts}, event handlers={output_html.event_handlers}, unsafe URLs={output_html.unsafe_urls}."
        ))
    else:
        checks.append(make_check("html_security", "passed", "hard"))

    allowed_tag_rules = rule_values(guidelines, "html_allowed_tags")
    if allowed_tag_rules:
        allowed_tags = {
            str(value).lower()
            for _, rule in allowed_tag_rules
            for value in (rule.get("value") if isinstance(rule.get("value"), list) else [rule.get("value")])
            if value
        }
        disallowed = sorted(set(output_html.tags) - allowed_tags)
        checks.append(make_check(
            "html_allowed_tags", "failed" if disallowed else "passed", "hard",
            "" if not disallowed else f"Disallowed HTML tags {disallowed}. Use only {sorted(allowed_tags)}."
        ))
    allowed_attribute_rules = rule_values(guidelines, "html_allowed_attributes")
    if allowed_attribute_rules:
        allowed_attributes = {
            str(value).lower()
            for _, rule in allowed_attribute_rules
            for value in (rule.get("value") if isinstance(rule.get("value"), list) else [rule.get("value")])
            if value
        }
        disallowed = sorted(set(output_html.attributes) - allowed_attributes)
        checks.append(make_check(
            "html_allowed_attributes", "failed" if disallowed else "passed", "hard",
            "" if not disallowed else f"Disallowed HTML attributes {disallowed}. Use only {sorted(allowed_attributes)}."
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
        elif value == "html_tags" and source_html.tags == output_html.tags:
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
