#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, locale_base, load_brand,
    read_catalog, resolve_workspace, slug, utc_now,
)
from shopify_connector import ShopifyConnector


QUERY = '''query MarketOnboarding($first: Int!) {
  markets(first: $first) {
    nodes {
      id
      handle
      name
      status
      type
      webPresences(first: 10) {
        nodes {
          id
          subfolderSuffix
          defaultLocale {
            locale
            name
            primary
            published
          }
          alternateLocales {
            locale
            name
            primary
            published
          }
        }
      }
    }
  }
}'''


def parse_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def mode_for(source_language: str, target_locale: str) -> str:
    return "localization_only" if locale_base(source_language) == locale_base(target_locale) else "translation"


def shopify_check(
    connector: ShopifyConnector | None, market_id: str, locale: str, shopify_locale: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if connector is None:
        return None, ["Shopify check skipped: the workspace is in pure mode."]
    result = connector.run_graphql(
        QUERY, {"first": 50}, operation_kind="read",
        operation_id=f"market_onboarding_{slug(market_id)}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}",
    )
    data = result["response"].get("data")
    nodes = data.get("markets", {}).get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        raise BrandLabError("Shopify response: markets.nodes is missing. Check the API response or fixture.")
    wanted = market_id.casefold()
    match = next((node for node in nodes if isinstance(node, dict) and (
        str(node.get("handle", "")).casefold() == wanted
        or str(node.get("name", "")).casefold() == wanted
        or str(node.get("id", "")).casefold().endswith(f"/{wanted}")
    )), None)
    if match is None:
        raise BrandLabError(
            f"Shopify market '{market_id}' was not found. Create and activate it in Shopify Markets, then rerun onboarding."
        )
    if str(match.get("status")) != "ACTIVE":
        raise BrandLabError(f"Shopify market '{market_id}' is {match.get('status')}. Activate it before onboarding.")
    locales: list[dict[str, Any]] = []
    for presence in match.get("webPresences", {}).get("nodes", []):
        if isinstance(presence, dict):
            default = presence.get("defaultLocale")
            if isinstance(default, dict):
                locales.append(default)
            alternates = presence.get("alternateLocales", [])
            if isinstance(alternates, list):
                locales.extend(row for row in alternates if isinstance(row, dict))
    available = {str(row.get("locale")) for row in locales if row.get("locale")}
    if shopify_locale not in available:
        raise BrandLabError(
            f"Shopify locale '{shopify_locale}' is not published for market '{market_id}'. "
            f"Available locales: {sorted(available) or '(none)'}."
        )
    checks = [
        f"Market found: {match.get('name')} ({match.get('id')}).",
        f"Market status: {match.get('status')}.",
        f"Locale enabled and published: {shopify_locale}.",
    ]
    return match, checks


def config_plan(args: argparse.Namespace, source_language: str, target_locale: str, translation_mode: str) -> dict[str, Any]:
    shopify_locale = args.shopify_locale or locale_base(target_locale)
    market = {
        "id": args.market_id, "market": args.market_id, "locale": target_locale,
        "shopify_locale": shopify_locale, "enabled": True,
        "source_strategy": args.source_strategy,
        "translation_mode": args.translation_mode or translation_mode,
        "review_required": not args.no_review, "audit_policy": args.audit_policy,
        "audit_mode": "auto",
    }
    source = {"default_locale": locale_base(source_language)}
    return {"append_market": market, "source": source, "market_key": args.market_id}


def content_plan(brand: dict[str, Any], target_locale: str, translation_mode: str) -> list[dict[str, Any]]:
    try:
        catalog = read_catalog(brand["_workspace"])
    except (KeyError, TypeError):
        catalog = []
    existing = [row for row in catalog if row.get("target_locale", "").lower() == target_locale.lower()]
    actionable = [row for row in existing if row.get("status") not in {"exported", "archived"}]
    if translation_mode == "localization_only":
        return [{
            "action": "market_localization_audit", "target_locale": target_locale,
            "items": len(actionable), "note": "No English-to-English translation task will be created.",
        }]
    return [
        {"action": "scan_source_content", "source_locale": brand["source_language"]},
        {"action": "import_target_locale", "target_locale": target_locale, "existing_items": len(existing)},
        {"action": "create_translation_jobs", "target_locale": target_locale, "items": len(actionable)},
    ]


def onboard(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    brand["_workspace"] = workspace
    market_key = str(args.market_id or "").strip()
    target_locale = str(args.locale or "").strip()
    if not market_key or not target_locale:
        raise BrandLabError("--market-id and --locale are required. Example: --market-id US --locale en-US.")
    if any(str(row.get("id") or row.get("market")).casefold() == market_key.casefold() for row in brand.get("markets", [])):
        raise BrandLabError(f"market '{market_key}' already exists in brand.yaml. Use the audit workflow instead.")
    translation_mode = args.translation_mode or mode_for(brand["source_language"], target_locale)
    if args.translation_mode and args.translation_mode != mode_for(brand["source_language"], target_locale) and args.translation_mode != "manual_only":
        raise BrandLabError(
            f"--translation-mode {args.translation_mode}: source and target languages indicate "
            f"{mode_for(brand['source_language'], target_locale)}. Use manual_only only for a deliberate exception."
        )
    shopify_locale = args.shopify_locale or locale_base(target_locale)
    connector = None
    checks: list[str] = []
    shopify_market = None
    if brand["mode"] == "shopify_controlled":
        if not args.skip_shopify_check:
            connector = ShopifyConnector(workspace, provider=args.provider, fixture=Path(args.fixture).resolve() if args.fixture else None)
            shopify_market, checks = shopify_check(connector, market_key, target_locale, shopify_locale)
        else:
            checks = ["Shopify check skipped by explicit operator request."]

    plan = config_plan(args, brand["source_language"], target_locale, translation_mode)
    content = content_plan(brand, target_locale, plan["append_market"]["translation_mode"])
    onboarding_id = f"market-onboarding-{slug(market_key)}-{utc_now()[:10]}"
    report = {
        "onboarding_id": onboarding_id, "created_at": utc_now(), "market": market_key,
        "target_locale": target_locale, "shopify_locale": shopify_locale,
        "source_language": brand["source_language"], "translation_mode": plan["append_market"]["translation_mode"],
        "config_updated": bool(args.confirm_update), "checks": checks,
        "shopify_market": shopify_market, "config_plan": plan, "content_plan": content,
        "guideline_recommendation": {
            "id": f"general-{slug(market_key)}", "resource_type": "*", "target_locale": target_locale,
            "market": market_key, "status": "active", "version": 1, "rules": [],
        },
        "glossary_recommendation": {
            "target_locale": target_locale, "status": "draft",
            "note": "Add locked brand terms and preferred local equivalents before review.",
        },
    }
    output_dir = workspace / "onboarding"
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / f"{onboarding_id}.json", report)

    yaml_market = json.loads(json.dumps(plan["append_market"]))
    yaml_source = json.loads(json.dumps(plan["source"]))
    lines = [
        "# Market Onboarding Plan", "",
        f"- Market: `{market_key}`", f"- Target locale: `{target_locale}`",
        f"- Shopify locale: `{shopify_locale}`", f"- Workflow: `{plan['append_market']['translation_mode']}`",
        f"- Human review required: `{plan['append_market']['review_required']}`", "",
        "## Shopify checks", "", *(f"- {check}" for check in checks), "",
        "## brand.yaml update", "", "```yaml", yaml.safe_dump({"source": yaml_source, "markets": [yaml_market]}, sort_keys=False).rstrip(), "```", "",
        "## Content plan", "",
        "| Action | Locale | Items | Note |", "|---|---|---:|---|",
    ]
    for row in content:
        lines.append(f"| `{row['action']}` | `{row.get('target_locale', brand['source_language'])}` | {row.get('items', '-')} | {row.get('note', '')} |")
    lines += [
        "", "## Next action", "",
        "Review this plan. Say “confirm this market onboarding” to apply the brand.yaml update; guideline and glossary edits remain explicit follow-up tasks." if not args.confirm_update else "The market registry update was applied. Run content scan or market audit next.",
    ]
    report_path = output_dir / f"{onboarding_id}.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if args.confirm_update:
        config_path = workspace / "brand.yaml"
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise BrandLabError(f"{config_path}: brand.yaml must contain a YAML object.")
        value.setdefault("markets", []).append(yaml_market)
        value.setdefault("source", {}).update(yaml_source)
        config_path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")

    append_history(workspace, "market_onboarding_planned", {
        "onboarding_id": onboarding_id, "market": market_key, "target_locale": target_locale,
        "translation_mode": plan["append_market"]["translation_mode"], "config_updated": bool(args.confirm_update),
    })
    print(json.dumps({
        "result": "ok", "onboarding_id": onboarding_id, "market": market_key, "target_locale": target_locale,
        "translation_mode": plan["append_market"]["translation_mode"], "config_updated": bool(args.confirm_update),
        "report": str(report_path),
    }, ensure_ascii=False))
    print("Next: review the onboarding plan, then confirm before applying brand.yaml changes." if not args.confirm_update else "Next: run content scan for a translation market, or localization audit for an English market.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or apply a Shopify market onboarding. Configuration changes require --confirm-update.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--market-id", required=True)
    parser.add_argument("--locale", required=True)
    parser.add_argument("--shopify-locale")
    parser.add_argument("--source-strategy", choices=["base", "bridge", "existing_translation"], default="base")
    parser.add_argument("--translation-mode", choices=["translation", "localization_only", "manual_only"])
    parser.add_argument("--audit-policy", choices=["standard", "seo_and_legal", "custom"], default="standard")
    parser.add_argument("--no-review", action="store_true", help="Explicitly disable mandatory review; not recommended.")
    parser.add_argument("--confirm-update", action="store_true", help="Apply the confirmed market to brand.yaml.")
    parser.add_argument("--skip-shopify-check", action="store_true")
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    parser.add_argument("--fixture", help="Fixture JSON containing the Shopify markets response.")
    return parser


def main() -> int:
    try:
        onboard(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
