#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

import yaml

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, atomic_write_text,
    load_brand, resolve_workspace, utc_now,
)

Runner = Callable[[list[str]], tuple[int, str, str]]


def default_runner(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(command, text=True, capture_output=True)
    return completed.returncode, completed.stdout, completed.stderr


def version_number(output: str) -> str:
    match = re.search(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", output)
    return match.group(0) if match else ""


def version_parts(output: str) -> tuple[int, int, int] | None:
    match = re.search(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", output)
    if not match:
        return None
    return tuple(int(match.group(index) or 0) for index in range(1, 4))  # type: ignore[return-value]


def fixture_checks(path: Path) -> dict[str, object]:
    from brandlab_lib import read_json
    value = read_json(path)
    required = {"node_version", "shopify_cli_version", "ai_toolkit_installed", "store_authenticated"}
    missing = sorted(required - set(value))
    if missing:
        raise BrandLabError(f"{path}: setup fixture is missing {missing}.")
    return value


def run_checks(args: argparse.Namespace) -> tuple[list[tuple[str, bool, str]], dict[str, object]]:
    checks: list[tuple[str, bool, str]] = []
    if args.fixture:
        fixture = fixture_checks(Path(args.fixture).expanduser().resolve())
        node_version = str(fixture["node_version"])
        cli_version = str(fixture["shopify_cli_version"])
        ai_ok = bool(fixture["ai_toolkit_installed"])
        store_ok = bool(fixture["store_authenticated"])
        command = ["fixture: node --version"]
    else:
        command = ["node", "--version"]
        code, stdout, stderr = default_runner(command)
        node_version = version_number(stdout + stderr)
        command = ["shopify", "--version"]
        code, stdout, stderr = default_runner(command)
        cli_version = version_number(stdout + stderr)
        plugin_code, plugin_stdout, plugin_stderr = default_runner(["codex", "plugin", "list"])
        combined = plugin_stdout + plugin_stderr
        ai_ok = bool(re.search(r"(?:shopify@openai-curated|shopify-plugin@shopify-ai-toolkit)\s+installed,\s+enabled", combined, flags=re.I))
        store_ok = False
        if args.store and cli_version:
            info_code, info_stdout, info_stderr = default_runner(["shopify", "store", "info", "--store", args.store])
            store_ok = info_code == 0

    parts = version_parts(node_version)
    node_ok = bool(parts and parts >= (18, 0, 0))
    checks.append((
        "Node.js 18+", node_ok,
        node_version if node_ok else "Install Node.js 18 or later: https://nodejs.org.",
    ))
    checks.append((
        "Shopify CLI", bool(cli_version),
        cli_version if cli_version else "Run: npm install -g @shopify/cli@latest",
    ))
    checks.append((
        "Shopify AI Toolkit plugin", ai_ok,
        "Installed and enabled." if ai_ok else "Run: codex plugin add shopify-plugin@shopify-ai-toolkit",
    ))
    checks.append((
        "Shopify CLI store authentication", store_ok,
        "Selected store is available." if store_ok else "Run: shopify store auth, then verify with shopify store info --store YOUR_STORE.myshopify.com.",
    ))
    return checks, {"node_version": node_version, "shopify_cli_version": cli_version}


def update_config(workspace: Path, args: argparse.Namespace, passed: bool) -> tuple[dict[str, object], bool]:
    brand_path = workspace / "brand.yaml"
    brand = load_brand(workspace)
    config = brand.get("shopify") if isinstance(brand.get("shopify"), dict) else {}
    current_store = config.get("store")
    if current_store and args.store and current_store != args.store:
        raise BrandLabError(
            f"{brand_path}: shopify.store is already {current_store}; passed {args.store}. "
            "Remove or update shopify.store deliberately before switching stores."
        )
    if args.enable_mutations and not args.reviewer:
        raise BrandLabError("--enable-mutations requires --reviewer to record explicit human responsibility.")
    mutations_allowed = bool(args.enable_mutations)

    selected_store = args.store or current_store
    shopify_config = {
        "enabled": bool(passed and selected_store and args.allow_reads),
        "setup_completed": bool(passed and selected_store and args.allow_reads),
        "store": args.store or current_store,
        "allow_reads": bool(passed and args.allow_reads),
        "allow_mutations": mutations_allowed,
        "max_batch_size": min(10, int(config.get("max_batch_size", args.max_batch_size))),
        "mutation_enabled_by": args.reviewer if args.enable_mutations else config.get("mutation_enabled_by"),
        "mutation_enabled_at": utc_now() if args.enable_mutations else config.get("mutation_enabled_at"),
        "last_setup_at": utc_now(),
    }
    if not passed:
        shopify_config["enabled"] = False
        shopify_config["setup_completed"] = False
    if not args.store and current_store:
        shopify_config["store"] = current_store
    brand["mode"] = "shopify_controlled"
    brand["shopify"] = shopify_config
    if args.enable_mutations:
        brand.setdefault("history_notes", []).append({
            "timestamp": utc_now(), "event": "shopify_mutations_enabled", "reviewer": args.reviewer,
        })
    atomic_write_text(brand_path, yaml.safe_dump(brand, sort_keys=False, allow_unicode=True))
    return shopify_config, bool(args.enable_mutations)


def render_report(
    workspace: Path, args: argparse.Namespace, checks: list[tuple[str, bool, str]],
    config: dict[str, object], mutations_changed: bool,
) -> Path:
    status = "ready" if config.get("setup_completed") else "blocked"
    next_action = (
        "Run shopify_discover.py to create a read-only discovery snapshot."
        if config.get("setup_completed")
        else "Complete the failed checks, then rerun shopify_setup.py."
    )
    if config.get("setup_completed") and not config.get("allow_mutations"):
        next_action += " Mutations remain disabled."
    if mutations_changed:
        next_action += f" Mutations were explicitly enabled by {args.reviewer}."
    values = {
        "generated_at": utc_now(),
        "workspace": str(workspace),
        "store": args.store or str(config.get("store") or ""),
        "status": status,
        "checks": "\n".join(f"- {'PASS' if ok else 'FAIL'} {name}: {detail}" for name, ok, detail in checks),
        "guidance": "\n".join([
            "```bash",
            "npm install -g @shopify/cli@latest",
            "codex plugin add shopify-plugin@shopify-ai-toolkit",
            "shopify store auth",
            "shopify store info --store YOUR_STORE.myshopify.com",
            "```",
        ]),
        "enabled": str(config.get("enabled")).lower(),
        "allow_reads": str(config.get("allow_reads")).lower(),
        "allow_mutations": str(config.get("allow_mutations")).lower(),
        "max_batch_size": str(config.get("max_batch_size", 10)),
        "next_action": next_action,
    }
    template = Path(__file__).resolve().parents[1] / "templates" / "shopify-setup-report.md"
    report = template.read_text(encoding="utf-8")
    for key, value in values.items():
        report = report.replace("{{" + key + "}}", value)
    report_path = workspace / "shopify" / "setup-report.md"
    atomic_write_text(report_path, report)
    return report_path


def setup(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    checks, versions = run_checks(args)
    passed = all(ok for _, ok, _ in checks)
    selected_store = args.store or (brand.get("shopify") or {}).get("store")
    if not selected_store:
        checks.append(("Selected store", False, "Pass --store YOUR_STORE.myshopify.com."))
        passed = False
    if not args.allow_reads:
        checks.append(("Read permission", False, "Pass --allow-reads to enable read-only discovery."))
        passed = False
    for directory in ["shopify", "shopify/discovery", "shopify/mutation-plans", "shopify/execution-results", "shopify/connector-output"]:
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    config, mutations_changed = update_config(workspace, args, passed)
    report_path = render_report(workspace, args, checks, config, mutations_changed)
    append_history(workspace, "shopify_setup_completed", {
        "passed": passed, "store": args.store, "versions": versions,
        "allow_reads": config.get("allow_reads"), "allow_mutations": config.get("allow_mutations"),
        "mutations_changed": mutations_changed, "reviewer": args.reviewer,
    })
    print(json.dumps({
        "result": "ok" if passed else "blocked", "workspace": str(workspace),
        "store": config.get("store"), "enabled": config.get("enabled"),
        "allow_reads": config.get("allow_reads"), "allow_mutations": config.get("allow_mutations"),
        "report": str(report_path),
    }))
    print("Next: " + ("run shopify_discover.py." if passed else "fix failed checks and rerun shopify_setup.py."))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect Shopify prerequisites and configure controlled execution.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--store", help="Selected myshopify.com domain.")
    parser.add_argument("--allow-reads", action="store_true", help="Enable read-only discovery after checks pass.")
    parser.add_argument("--enable-mutations", action="store_true", help="Explicitly enable planned mutations; requires --reviewer.")
    parser.add_argument("--reviewer", help="Reviewer or operator when --enable-mutations is used.")
    parser.add_argument("--max-batch-size", type=int, default=10)
    parser.add_argument("--fixture", help="Test fixture with setup check results; prevents live command execution.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        setup(args)
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
