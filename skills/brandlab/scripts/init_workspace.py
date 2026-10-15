#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, atomic_write_text, load_yaml,
    locale_base, slug, utc_now,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]


def parse_market(value: str) -> dict[str, object]:
    if ":" not in value:
        raise BrandLabError(f"--target-market {value}: invalid format. Use MARKET:locale, for example IT:it-IT.")
    market, locale = [part.strip() for part in value.split(":", 1)]
    if not market or not locale:
        raise BrandLabError(f"--target-market {value}: market and locale cannot be empty. Use MARKET:locale.")
    return {"market": market, "locale": locale, "enabled": True}


def make_guideline(guideline_id: str, name: str, resource_type: str, market: str, locale: str, version: int = 1) -> dict[str, object]:
    rules: list[dict[str, object]] = [
        {"type": "instruction", "rule": "Keep the brand name unchanged."},
        {"type": "preserve", "value": "placeholders"},
    ]
    if resource_type == "product_title":
        rules.extend([
            {"type": "max_length", "value": 80},
            {"type": "forbidden_regex", "pattern": r"(?i)\b(saldi|sconto)\b"},
        ])
    else:
        rules.extend([
            {"type": "preserve", "value": "html_tags"},
            {"type": "min_length", "value": 3},
        ])
    return {
        "id": guideline_id, "name": name, "resource_type": resource_type,
        "target_locale": locale, "market": market, "status": "active",
        "version": version, "rules": rules,
    }


def prompt_if_missing(args: argparse.Namespace) -> None:
    if args.brand and args.mode and args.source_language and args.working_language:
        return
    print("BrandLab setup interview")
    if not args.brand:
        args.brand = input("Brand name: ").strip()
    if not args.mode:
        args.mode = input("Mode [pure]: ").strip() or "pure"
    if not args.source_language:
        args.source_language = input("Source language [en]: ").strip() or "en"
    if not args.working_language:
        args.working_language = input("Working language [zh-CN]: ").strip() or "zh-CN"
    if not args.target_market:
        raw = input("Target market as MARKET:locale [IT:it-IT]: ").strip() or "IT:it-IT"
        args.target_market.append(raw)


def initialize(args: argparse.Namespace) -> None:
    prompt_if_missing(args)
    workspace = Path(args.workspace).expanduser().resolve()
    brand_path = workspace / "brand.yaml"
    created: list[str] = []
    skipped: list[str] = []
    checks: list[str] = []

    if args.mode not in {"pure", "shopify_controlled"}:
        raise BrandLabError(f"--mode {args.mode}: mode must be pure or shopify_controlled.")

    markets = [parse_market(value) for value in args.target_market]
    if not markets:
        raise BrandLabError("--target-market: add at least one market using MARKET:locale.")

    brand = {
        "brand": args.brand,
        "mode": args.mode,
        "source_language": args.source_language,
        "working_language": args.working_language,
        "default_tone": args.default_tone,
        "markets": markets,
    }

    if brand_path.exists():
        existing = load_yaml(brand_path)
        if not isinstance(existing, dict):
            raise BrandLabError(f"{brand_path}: top level must be a mapping.")
        if existing != brand:
            skipped.append("brand.yaml (already exists)")
            checks.append("PASS Existing brand.yaml was preserved.")
    else:
        atomic_write_text(brand_path, yaml.safe_dump(brand, sort_keys=False, allow_unicode=True))
        created.append("brand.yaml")
        checks.append("PASS brand.yaml created.")

    required_dirs = [
        "guidelines", "glossary", "inbox", "content", "jobs", "approvals",
        "exports", "reports", "history",
    ]
    for relative in required_dirs:
        path = workspace / relative
        if path.exists():
            continue
        path.mkdir(parents=True, exist_ok=True)
        created.append(relative + "/")

    for source, destination in [
        (SKILL_ROOT / "templates" / "glossary.csv", workspace / "glossary" / "glossary.csv"),
        (SKILL_ROOT / "templates" / "review.md", workspace / "templates-review.md"),
    ]:
        if destination.exists():
            skipped.append(str(destination.relative_to(workspace)))
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(destination, source.read_text(encoding="utf-8"))
            created.append(str(destination.relative_to(workspace)))

    catalog = workspace / "content" / "catalog.jsonl"
    if not catalog.exists():
        catalog.touch()
        created.append("content/catalog.jsonl")
    else:
        skipped.append("content/catalog.jsonl")

    history = workspace / "history" / "events.jsonl"
    if not history.exists():
        history.touch()
        created.append("history/events.jsonl")
    else:
        skipped.append("history/events.jsonl")

    for market in markets:
        locale = str(market["locale"])
        market_name = str(market["market"])
        language = locale_base(locale)
        guidelines = [
            make_guideline(
                f"product-title-{language}", f"Product Title — {locale}",
                "product_title", market_name, locale,
            ),
            make_guideline(
                f"product-description-{language}", f"Product Description — {locale}",
                "product_description", market_name, locale,
            ),
        ]
        for guideline in guidelines:
            destination = workspace / "guidelines" / f"{guideline['id']}-v{guideline['version']}.yaml"
            if destination.exists():
                skipped.append(str(destination.relative_to(workspace)))
                continue
            atomic_write_text(destination, yaml.safe_dump(guideline, sort_keys=False, allow_unicode=True))
            created.append(str(destination.relative_to(workspace)))

    next_action = "Put source files in brandlab/inbox/, then run import_content.py."
    if args.mode == "shopify_controlled":
        next_action = (
            "Run shopify_setup.py with --store, --allow-reads, and --fixture for tests; "
            "do not authenticate or install tools without explicit confirmation."
        )
        checks.append("INFO shopify_controlled selected; Shopify setup is required before discovery.")

    report_values = {
        "generated_at": utc_now(),
        "mode": args.mode,
        "workspace": str(workspace),
        "status": "ready",
        "checks": "\n".join(checks),
        "created_files": "\n".join(f"- {item}" for item in created) or "- None",
        "skipped_files": "\n".join(f"- {item}" for item in skipped) or "- None",
        "next_action": next_action,
    }
    report = (SKILL_ROOT / "templates" / "setup-report.md").read_text(encoding="utf-8")
    for key, value in report_values.items():
        report = report.replace("{{" + key + "}}", value)
    atomic_write_text(workspace / "setup-report.md", report)
    created.append("setup-report.md")

    append_history(workspace, "workspace_initialized", {
        "workspace": str(workspace), "mode": args.mode,
        "created": created, "skipped": skipped,
    })
    print(f"BrandLab workspace ready: {workspace}")
    print(f"Created: {len(created)} item(s); skipped: {len(skipped)} existing item(s)")
    print(f"Next: {next_action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize a BrandLab Translation Skill workspace.")
    parser.add_argument("--workspace", default="brandlab", help="BrandLab workspace directory.")
    parser.add_argument("--brand")
    parser.add_argument("--mode", default="pure")
    parser.add_argument("--source-language")
    parser.add_argument("--working-language")
    parser.add_argument("--default-tone", default="premium, confident, minimal")
    parser.add_argument("--target-market", action="append", default=[])
    return parser


def main() -> int:
    try:
        initialize(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
