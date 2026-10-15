#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from brandlab_lib import (    validate_schema,

    atomic_write_text,
    BrandLabError, append_history, atomic_write_json, load_brand, market_for_locale,
    read_catalog, read_jsonl_contract, resolve_workspace, sha256_text, stable_id, utc_now, write_catalog,
    write_jsonl,
    write_jsonl_contract,
)
from shopify_connector import ShopifyConnector


SUPPORTED_RESOURCES = {"product", "collection", "page", "blog", "market"}


def resource_query(resources: set[str], first: int = 100) -> str:
    sections = ["query BrandLabDiscovery {", "  shopLocales { locale name primary published }"]
    if "product" in resources:
        sections += [
            "  products(first: %d) { pageInfo { hasNextPage } edges { node {" % first,
            "    id title descriptionHtml",
            "    seo { title description }",
            "  } } }",
        ]
    if "collection" in resources:
        sections += [
            "  collections(first: %d) { pageInfo { hasNextPage } edges { node {" % first,
            "    id title descriptionHtml",
            "  } } }",
        ]
    if "page" in resources:
        sections += [
            "  pages(first: %d) { edges { node {" % first,
            "    id title body",
            "  } } }",
        ]
    if "blog" in resources:
        sections += [
            "  blogs(first: %d) { edges { node {" % first,
            "    id title handle",
            "    articles(first: %d) { edges { node { id title body } } }" % first,
            "  } } }",
        ]
    if "market" in resources:
        sections += [
            "  markets(first: %d) { nodes {" % first,
            "    id name enabledLanguages { code enabled }",
            "  } }",
        ]
    sections.append("}")
    return "\n".join(sections)


def edge_nodes(value: object, container: str) -> list[dict]:
    if not isinstance(value, dict):
        return []
    connection = value.get(container)
    if not isinstance(connection, dict):
        return []
    edges = connection.get("edges")
    if not isinstance(edges, list):
        return []
    return [edge.get("node", {}) for edge in edges if isinstance(edge, dict) and isinstance(edge.get("node"), dict)]


def normalized_nodes(data: dict) -> list[dict]:
    nodes: list[dict] = []
    for node in edge_nodes(data, "products"):
        nodes.append({"kind": "product", "node": node})
    for node in edge_nodes(data, "collections"):
        nodes.append({"kind": "collection", "node": node})
    for node in edge_nodes(data, "pages"):
        nodes.append({"kind": "page", "node": node})
    for item in edge_nodes(data, "blogs"):
        nodes.append({"kind": "blog", "node": item})
        for article in edge_nodes(item.get("articles", {}), "articles"):
            nodes.append({"kind": "blog_article", "node": article, "parent": item})
    return nodes


def resource_records(data: dict, target_locale: str, market: str) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    warnings: list[str] = []
    for wrapper in normalized_nodes(data):
        kind = wrapper["kind"]
        node = wrapper["node"]
        resource_id = str(node.get("id", ""))
        if not resource_id.startswith("gid://shopify/"):
            warnings.append(f"Skipped {kind} node without a valid Shopify gid.")
            continue
        fields: list[tuple[str, str]] = []
        if kind == "product":
            fields = [
                ("product_title", "title", str(node.get("title", "") or "")),
                ("product_description", "descriptionHtml", str(node.get("descriptionHtml", "") or "")),
                ("seo_title", "seo.title", str((node.get("seo") or {}).get("title", "") or "")),
                ("seo_description", "seo.description", str((node.get("seo") or {}).get("description", "") or "")),
            ]
        elif kind == "collection":
            fields = [
                ("collection_title", "title", str(node.get("title", "") or "")),
                ("collection_description", "descriptionHtml", str(node.get("descriptionHtml", "") or "")),
            ]
        elif kind == "page":
            fields = [
                ("page_title", "title", str(node.get("title", "") or "")),
                ("page_body", "body", str(node.get("body", "") or "")),
            ]
        elif kind in {"blog", "blog_article"}:
            if kind == "blog":
                fields = [("blog_title", "title", str(node.get("title", "") or ""))]
            else:
                fields = [
                    ("blog_title", "title", str(node.get("title", "") or "")),
                    ("blog_body", "body", str(node.get("body", "") or "")),
                ]
        for resource_type, field, source in fields:
            if not source.strip():
                continue
            records.append({
                "shopify_resource_id": resource_id,
                "parent_shopify_resource_id": str(wrapper.get("parent", {}).get("id", "")),
                "shopify_field": field,
                "resource_type": resource_type,
                "external_id": resource_id,
                "source_locale": "und",
                "target_locale": target_locale,
                "market": market,
                "source": source,
                "digest": sha256_text(source),
                "context": {
                    "shopify_resource_id": resource_id,
                    "shopify_field": field,
                    "shopify_handle": str(node.get("handle", "")),
                },
            })
    return records, warnings


def import_discovery(workspace: Path, records: list[dict]) -> tuple[int, int, int]:
    catalog = read_catalog(workspace)
    by_id = {item["id"]: item for item in catalog if isinstance(item, dict) and item.get("id")}
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    discovery_file = "shopify/discovery/latest-source-content.jsonl"
    now = utc_now()
    for record in records:
        item_id = stable_id(discovery_file, record["external_id"], record["resource_type"], record["target_locale"])
        old = by_id.get(item_id)
        item_context = dict(record.get("context", {}))
        item_context["discovery_file"] = discovery_file
        if old and old.get("digest") == record["digest"]:
            old.update({
                "source_file": discovery_file, "context": item_context,
                "source_locale": record["source_locale"], "status": "unchanged", "updated_at": now,
            })
            counts["unchanged"] += 1
        elif old:
            old.update({
                "source": record["source"], "digest": record["digest"],
                "source_file": discovery_file, "context": item_context,
                "source_locale": record["source_locale"], "status": "changed", "updated_at": now,
            })
            counts["changed"] += 1
        else:
            item = {
                "id": item_id,
                "external_id": record["external_id"],
                "source_file": discovery_file,
                "resource_type": record["resource_type"],
                "source_locale": record["source_locale"],
                "target_locale": record["target_locale"],
                "market": record["market"],
                "source": record["source"],
                "context": item_context,
                "digest": record["digest"],
                "status": "new",
                "imported_at": now,
                "updated_at": now,
            }
            by_id[item_id] = item
            catalog.append(item)
            counts["new"] += 1
    write_catalog(workspace, catalog)
    return counts["new"], counts["changed"], counts["unchanged"]


def discover(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    if brand.get("mode") != "shopify_controlled":
        raise BrandLabError(f"{workspace / 'brand.yaml'}: mode must be shopify_controlled. Run shopify_setup.py.")
    requested = set(args.resource_type or ["product"])
    unknown = requested - SUPPORTED_RESOURCES
    if unknown:
        raise BrandLabError(f"--resource-type {sorted(unknown)}: supported resources are {sorted(SUPPORTED_RESOURCES)}.")
    target_locale = args.target_locale
    market = market_for_locale(brand, target_locale)

    query_path = workspace / "shopify" / "discovery" / "discovery-query.graphql"
    query_path.parent.mkdir(parents=True, exist_ok=True)
    query = resource_query(requested)
    atomic_write_text(query_path, query + "\n")

    operation_id = f"discovery_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    connector = ShopifyConnector(
        workspace,
        provider=args.provider,
        fixture=Path(args.fixture).expanduser().resolve() if args.fixture else None,
    )
    result = connector.run_graphql(query, {}, operation_kind="read", operation_id=operation_id)
    response = result["response"]
    data = response.get("data")
    if not isinstance(data, dict):
        # Shopify CLI store execute returns fields at the top level without a "data" wrapper.
        data = response if isinstance(response, dict) else {}
    if not data:
        raise BrandLabError(f"Discovery {operation_id}: Shopify response data is missing. Inspect {result['output_path']}.")
    raw_records, warnings = resource_records(data, target_locale, market)
    if not raw_records and requested != {"market"}:
        raise BrandLabError(f"Discovery {operation_id}: no translatable fields were returned. Check the query or fixture.")

    _locales = data.get("shopLocales") or []
    _primary = next((l.get("locale") for l in _locales if isinstance(l, dict) and l.get("primary")), None)
    _source = args.source_locale or _primary or "en"
    for record in raw_records:
        record["source_locale"] = _source
    records_path = workspace / "shopify" / "discovery" / f"{operation_id}-resources.jsonl"
    write_jsonl_contract(records_path, raw_records, "shopify-discovery-record")
    new_count, changed_count, unchanged_count = import_discovery(workspace, raw_records)

    markets = data.get("markets")
    market_nodes = []
    if isinstance(markets, dict) and isinstance(markets.get("nodes"), list):
        market_nodes = [node for node in markets["nodes"] if isinstance(node, dict)]

    discovery_id = operation_id
    manifest = {
        "discovery_id": discovery_id,
        "store": connector.config["store"],
        "created_at": utc_now(),
        "query_path": str(query_path.relative_to(workspace)),
        "raw_output_path": str(result["output_path"].relative_to(workspace)),
        "resources": raw_records,
        "markets": market_nodes,
        "warnings": warnings,
    }
    manifest_path = workspace / "shopify" / "discovery" / f"{discovery_id}.json"
    validate_schema("shopify-discovery", manifest, manifest_path)
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(workspace / "shopify" / "discovery" / "latest.json", manifest)
    latest_source = workspace / "shopify" / "discovery" / "latest-source-content.jsonl"
    atomic_write_text(latest_source, records_path.read_text(encoding="utf-8"))

    report_lines = [
        "# Shopify Discovery Report", "",
        f"- Discovery ID: `{discovery_id}`", f"- Store: `{connector.config['store']}`",
        f"- Target locale: `{target_locale}`", f"- Resources requested: `{', '.join(sorted(requested))}`",
        f"- New: {new_count}", f"- Changed: {changed_count}", f"- Unchanged: {unchanged_count}",
        f"- Raw output: `{result['output_path'].relative_to(workspace)}`", "",
        "## Resource counts", "",
        "| Resource | Count |", "|---|---:|",
    ]
    for resource_type, count in sorted(Counter(record["resource_type"] for record in raw_records).items()):
        report_lines.append(f"| {resource_type} | {count} |")
    if warnings:
        report_lines += ["", "## Warnings", ""]
        report_lines.extend(f"- {warning}" for warning in warnings)
    report_lines += ["", "## Next action", "", "Run existing-content audit or create a translation job."]
    report_path = workspace / "shopify" / "discovery" / f"{discovery_id}-report.md"
    atomic_write_text(report_path, "\n".join(report_lines) + "\n")
    append_history(workspace, "shopify_discovery_completed", {
        "discovery_id": discovery_id, "store": connector.config["store"],
        "resources": sorted(requested), "counts": {
            "new": new_count, "changed": changed_count, "unchanged": unchanged_count,
        },
        "raw_output": str(result["output_path"].relative_to(workspace)),
    })
    print(json.dumps({
        "result": "ok", "discovery_id": discovery_id, "store": connector.config["store"],
        "counts": {"new": new_count, "changed": changed_count, "unchanged": unchanged_count},
        "manifest": str(manifest_path), "report": str(report_path),
    }))
    print(f"Next: python3 scripts/create_job.py --workspace {workspace} --target-locale {target_locale}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read translation-relevant Shopify content through Shopify CLI.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--target-locale", required=True)
    parser.add_argument("--source-locale", help="Overrides Shopify shop primary locale in the discovery response.")
    parser.add_argument("--resource-type", nargs="*", choices=sorted(SUPPORTED_RESOURCES), default=["product"])
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    parser.add_argument("--fixture", help="Fixture JSON containing a Shopify GraphQL response; no live CLI call is made.")
    return parser


def main() -> int:
    try:
        discover(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
