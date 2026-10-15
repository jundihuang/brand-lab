from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from brandlab_lib import BrandLabError, atomic_write_json, read_json, sha256_text
from brandlab_lib import validate_schema


PRODUCT_FIELD_MAP = {
    "product_title": "title",
    "product_description": "descriptionHtml",
    "seo_title": "seo.title",
    "seo_description": "seo.description",
}
TRANSLATION_PLAN_TYPE = "translation_register"


def canonical_digest(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def approval_digest(approval_state: dict[str, Any], item_id: str) -> str:
    decisions = [
        decision for decision in approval_state.get("decisions", [])
        if isinstance(decision, dict) and decision.get("item_id") == item_id
    ]
    if not decisions:
        raise BrandLabError(f"approval: item {item_id} has no recorded approval decision.")
    latest = decisions[-1]
    if latest.get("decision") != "approve" or latest.get("recorded_status") != "approved":
        raise BrandLabError(f"approval: item {item_id} latest decision is not approved.")
    if not latest.get("source_digest"):
        raise BrandLabError(f"approval: item {item_id} has no source digest.")
    return canonical_digest(decisions)


def load_plan(path: Path) -> dict[str, Any]:
    plan = read_json(path)
    if not isinstance(plan, dict):
        raise BrandLabError(f"{path}: mutation plan must be a JSON object.")
    required = {"batch_id", "mode", "store", "status", "target_locale", "max_batch_size", "operations", "graphql"}
    missing = sorted(required - set(plan))
    if missing:
        raise BrandLabError(f"{path}: mutation plan is missing {missing}.")
    if plan["mode"] != "shopify_controlled":
        raise BrandLabError(f"{path}: plan mode must be shopify_controlled.")
    if plan.get("plan_type") not in {None, TRANSLATION_PLAN_TYPE, "product_update"}:
        raise BrandLabError(f"{path}: unsupported plan type {plan.get('plan_type')}.")
    if int(plan["max_batch_size"]) > 10:
        raise BrandLabError(f"{path}: max_batch_size cannot exceed 10.")
    if len(plan["operations"]) > int(plan["max_batch_size"]):
        raise BrandLabError(f"{path}: operation count exceeds max_batch_size.")
    if not isinstance(plan["graphql"], dict) or not isinstance(plan["graphql"].get("query"), str):
        raise BrandLabError(f"{path}: graphql.query is required.")
    validate_schema("mutation-plan", plan, path)
    return plan


def save_plan(path: Path, plan: dict[str, Any]) -> None:
    from brandlab_lib import atomic_write_json
    plan["updated_at"] = utc_now_safe()
    validate_schema("mutation-plan", plan, path)
    atomic_write_json(path, plan)


def utc_now_safe() -> str:
    from brandlab_lib import utc_now
    return utc_now()


def plan_digest(plan: dict[str, Any]) -> str:
    scoped = {key: plan.get(key) for key in [
        "batch_id", "job_id", "mode", "store", "target_locale", "max_batch_size", "graphql", "operations"
    ]}
    return canonical_digest(scoped)
