#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, find_market, locale_base,
    resolve_workspace, sha256_text, slug, utc_now, write_jsonl,
)
from shopify_connector import ShopifyConnector

QUERY = '''query ExistingTranslations($locale: String!, $first: Int!, $resourceType: TranslatableResourceType!) {
  shopLocales {
    locale
    name
    primary
    published
  }
  translatableResources(first: $first, resourceType: $resourceType) {
    nodes {
      resourceId
      translatableContent {
        key
        value
        digest
        locale
      }
      translations(locale: $locale) {
        key
        value
        market { id }
        outdated
        updatedAt
      }
    }
  }
}'''

RESOURCE_TYPES = {
    'Product': 'product',
    'Collection': 'collection',
    'Page': 'page',
    'Blog': 'blog',
    'Article': 'blog_article',
    'Metaobject': 'metaobject',
}
UNVERIFIABLE = [
    'ai_semantic_equivalence', 'ai_omission', 'ai_addition', 'ai_mistranslation',
]


def value_text(value: Any) -> str:
    return '' if value is None else str(value)


def resource_type(resource_id: str) -> str:
    parts = resource_id.split('/')
    kind = parts[-2] if len(parts) >= 2 else parts[-1]
    if kind in RESOURCE_TYPES:
        return RESOURCE_TYPES[kind]
    return re.sub(r'(?<!^)(?=[A-Z])', '_', kind).casefold()


def _market_id(row: dict[str, Any]) -> str | None:
    market = row.get('market')
    if isinstance(market, dict):
        return value_text(market.get('id')) or None
    return None


def choose_translation(rows: Any, key: str, shopify_market_id: str | None) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    matches = [row for row in rows if isinstance(row, dict) and row.get('key') == key]
    if shopify_market_id:
        for row in matches:
            if _market_id(row) == shopify_market_id:
                return row
    for row in matches:
        if _market_id(row) is None:
            return row
    return matches[0] if matches else None


def choose_source(rows: Any, key: str, source_locale: str) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    wanted = source_locale.casefold()
    matches = [row for row in rows if isinstance(row, dict) and row.get('key') == key]
    for row in matches:
        if value_text(row.get('locale')).casefold() == wanted:
            return row
    for row in matches:
        if locale_base(value_text(row.get('locale'))) == locale_base(source_locale):
            return row
    return None


def choose_any_content(node: dict[str, Any]) -> dict[str, Any] | None:
    content = node.get('translatableContent')
    return content[0] if isinstance(content, list) and content and isinstance(content[0], dict) else None


def load_reference_rows(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    if resolved.suffix.casefold() == ".jsonl":
        from brandlab_lib import read_jsonl
        rows = read_jsonl(resolved)
    else:
        from brandlab_lib import read_json
        value = read_json(resolved)
        rows = value.get("references", []) if isinstance(value, dict) and isinstance(value.get("references"), list) else value
        if not isinstance(rows, list):
            raise BrandLabError(f"{resolved}: reference file must contain a JSON array or a references array.")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("shopify_resource_id") or not row.get("field_key") or not row.get("source_text"):
            raise BrandLabError(f"{resolved}: each reference needs shopify_resource_id, field_key, and source_text.")
        result[(str(row["shopify_resource_id"]), str(row["field_key"]))] = row
    return result


def load_history_references(workspace: Path, job_id: str | None) -> dict[tuple[str, str], dict[str, Any]]:
    if not job_id:
        return {}
    sources_dir = workspace / "jobs" / job_id / "sources"
    if not sources_dir.is_dir():
        raise BrandLabError(f"{sources_dir}: history job not found. Check the job ID.")
    from brandlab_lib import read_json
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sources_dir.glob("*.json"):
        row = read_json(path)
        resource_id = row.get("shopify_resource_id")
        field_key = row.get("field_key")
        if resource_id and field_key and row.get("source_available") is True and row.get("source"):
            result[(str(resource_id), str(field_key))] = {
                **row,
                "source_text": row["source"],
                "source_origin": "history_job",
            }
    if not result:
        raise BrandLabError(f"{sources_dir}: history job has no matching source references with Shopify resource IDs.")
    return result


def snapshot_records(
    data: dict[str, Any], market_entry: dict[str, Any], source_locale: str, market_id: str,
    shopify_market_id: str | None, references: dict[tuple[str, str], dict[str, Any]] | None = None,
    resource_filter: set[str] | None = None, shopify_locale_override: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    resources = data.get('translatableResources') or {}
    nodes = resources.get('nodes') if isinstance(resources, dict) else None
    if not isinstance(nodes, list):
        raise BrandLabError('Shopify response: translatableResources.nodes is missing. Check the API response or fixture.')
    for node in nodes:
        if not isinstance(node, dict):
            warnings.append('Skipped one non-object translatable resource node.')
            continue
        resource_id = value_text(node.get('resourceId'))
        if not resource_id.startswith('gid://shopify/'):
            warnings.append(f'Skipped resource without a valid Shopify gid: {resource_id or "(empty)"}')
            continue
        contents = node.get('translatableContent')
        if not isinstance(contents, list):
            warnings.append(f'Skipped resource without translatableContent: {resource_id}')
            continue
        translations = node.get('translations')
        if resource_filter and resource_type(resource_id) not in resource_filter:
            continue
        for content in contents:
            if not isinstance(content, dict) or not content.get('key'):
                warnings.append(f'Skipped invalid translatable content on {resource_id}.')
                continue
            key = value_text(content['key'])
            translation = choose_translation(translations, key, shopify_market_id)
            source = choose_source([content], key, source_locale)
            external = (references or {}).get((resource_id, key))
            external_origin = None
            external_digest = None
            external_text = None
            if source is None and external:
                source = external
                external_origin = str(external.get('source_origin') or 'uploaded_reference')
                external_text = value_text(external.get('source_text'))
                external_digest = value_text(external.get('source_digest')) or None
            if source is external:
                source_text = external_text
                source_digest = external_digest
            else:
                source_text = value_text(source.get('value')) if source else ''
                source_digest = (value_text(source.get('digest')) or None) if source else None
            published = value_text(translation.get('value')) if translation else ''
            records.append({
                'record_id': f'{resource_id}:{market_id}:{key}',
                'market': market_id,
                'market_id': market_entry.get('id'),
                'target_locale': market_entry['locale'],
                'shopify_locale': shopify_locale_override or market_entry.get('shopify_locale') or locale_base(market_entry['locale']),
                'resource_type': resource_type(resource_id),
                'shopify_resource_id': resource_id,
                'field_key': key,
                'source_available': bool(source),
                'source_origin': ('shopify_base' if not external_origin else external_origin) if source else 'none',
                'source_locale': value_text(source.get('locale')) if source else source_locale,
                'source_text': source_text,
                'source_digest': source_digest,
                'source_reference_digest': external_digest,
                'translatable_content_digest': value_text(content.get('digest')) or None,
                'published_locale': market_entry['locale'],
                'published_text': published,
                'published_digest': value_text(translation.get('digest')) if translation and translation.get('digest') else None,
                'shopify_market_id': translation.get('marketId') if translation else None,
                'outdated': translation.get('outdated') is True if translation else None,
                'shopify_updated_at': translation.get('updatedAt') if translation else None,
                'audit_mode': 'paired_source' if source else 'target_only',
                'unverifiable_checks': [] if source else UNVERIFIABLE,
                'discovered_at': utc_now(),
            })
    if not records:
        raise BrandLabError('Shopify discovery: no translatable fields were returned. Check the market, locale, permissions, or fixture.')
    return records, warnings


def discover_existing(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand_safe(workspace)
    market_entry = find_market(brand, args.market)
    if market_entry.get('enabled') is not True:
        raise BrandLabError(f"market '{args.market}': is disabled in brand.yaml. Enable it before discovery.")
    target_locale = args.locale or str(market_entry['locale'])
    shopify_locale = args.shopify_locale or str(market_entry.get('shopify_locale') or locale_base(target_locale))
    source_locale = args.source_locale or str(brand.get('source_language') or 'en')

    operation_id = f"existing_{slug(market_entry.get('id') or market_entry['market'])}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
    connector = ShopifyConnector(
        workspace,
        provider=args.provider,
        fixture=Path(args.fixture).expanduser().resolve() if args.fixture else None,
    )
    references = load_reference_rows(Path(args.reference_file).expanduser().resolve() if args.reference_file else None)
    references.update(load_history_references(workspace, args.history_job))
    variables: dict[str, Any] = {'locale': shopify_locale, 'first': args.limit, 'resourceType': 'PRODUCT'}
    result = connector.run_graphql(
        QUERY, variables,
        operation_kind='read', operation_id=operation_id,
    )
    response = result['response']
    data = response.get('data')
    if not isinstance(data, dict):
        # Shopify CLI store execute returns fields at the top level without a "data" wrapper.
        data = response if isinstance(response, dict) else {}
    if not data:
        raise BrandLabError(f"Discovery {operation_id}: Shopify response data is missing. Inspect {result['output_path']}.")

    records, warnings = snapshot_records(
        data, market_entry, source_locale,
        str(market_entry.get('id') or market_entry['market']), args.shopify_market_id,
        references, set(args.resource_type) if args.resource_type else None,
        shopify_locale_override=shopify_locale,
    )
    output_dir = workspace / 'shopify' / 'existing-translations'
    market_slug = slug(str(market_entry.get('id') or market_entry['market']))
    records_path = output_dir / f'{market_slug}.jsonl'
    write_jsonl(records_path, records)

    source_records = sum(item['source_available'] for item in records)
    if source_records == len(records):
        availability = 'found'
    elif source_records == 0:
        availability = 'not_found'
    else:
        availability = 'mixed'
    audit_mode = (
        'paired_source' if source_records == len(records)
        else 'target_only' if source_records == 0
        else 'mixed'
    )
    manifest = {
        'discovery_id': operation_id,
        'store': connector.config['store'],
        'created_at': utc_now(),
        'market': str(market_entry.get('id') or market_entry['market']),
        'target_locale': target_locale,
        'shopify_locale': shopify_locale,
        'shopify_market_id': args.shopify_market_id,
        'source_locale': source_locale,
        'source_origins': sorted({row['source_origin'] for row in records}),
        'resource_type_filter': sorted(args.resource_type) if args.resource_type else [],
        'audit_mode': audit_mode,
        'source_availability': availability,
        'record_count': len(records),
        'records_path': str(records_path.relative_to(workspace)),
        'raw_response_path': str(result['output_path'].relative_to(workspace)),
        'warnings': warnings,
    }
    manifest_path = output_dir / f'{market_slug}-manifest.json'
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(output_dir / f'{market_slug}-latest.json', manifest)
    report = [
        '# Existing Translation Discovery', '',
        f"- Discovery ID: `{operation_id}`",
        f"- Market: `{market_entry.get('id') or market_entry['market']}`",
        f"- Target locale: `{target_locale}`",
        f"- Shopify locale: `{shopify_locale}`",
        f"- Source availability: `{availability}`",
        f"- Records: {len(records)}", '',
        '## Next action', '',
        'Run `audit_existing_translations.py` to create the read-only translation health report.',
    ]
    if warnings:
        report += ['', '## Warnings', ''] + [f'- {warning}' for warning in warnings]
    report_path = output_dir / f'{market_slug}-report.md'
    report_path.write_text('\n'.join(report) + '\n', encoding='utf-8')
    append_history(workspace, 'existing_translation_discovered', {
        'discovery_id': operation_id, 'market': market_entry.get('id') or market_entry['market'],
        'records': len(records), 'source_availability': availability,
    })
    print(json.dumps({
        'result': 'ok', 'discovery_id': operation_id, 'market': market_entry.get('id') or market_entry['market'],
        'records': len(records), 'source_availability': availability,
        'records_path': str(records_path), 'manifest': str(manifest_path), 'report': str(report_path),
    }, ensure_ascii=False))
    print(f"Next: python3 scripts/audit_existing_translations.py --workspace {workspace} --market {market_entry.get('id') or market_entry['market']}")


def load_brand_safe(workspace: Path):
    # Imported late for consistency with other CLI scripts.
    from brandlab_lib import load_brand
    return load_brand(workspace)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Discover existing Shopify market translations. This command never writes to Shopify.')
    parser.add_argument('--workspace', default='brandlab')
    parser.add_argument('--market', required=True, help='Market id or market code from brand.yaml.')
    parser.add_argument('--locale', help='Override BrandLab target locale.')
    parser.add_argument('--shopify-locale', help='Override Shopify locale for the GraphQL query.')
    parser.add_argument('--shopify-market-id', help='Prefer translations published to this Shopify market ID.')
    parser.add_argument('--source-locale', help='Override source locale; defaults to source_language.')
    parser.add_argument('--reference-file', help='JSON or JSONL uploaded source references.')
    parser.add_argument('--history-job', help='Existing BrandLab job ID used as a source reference.')
    parser.add_argument('--resource-type', nargs='*', help='Optional local resource-type filter.')
    parser.add_argument('--limit', type=int, default=20, help='Maximum Shopify resources to read in this discovery page.')
    parser.add_argument('--provider', default='live', choices=['live', 'fixture'])
    parser.add_argument('--fixture', help='Fixture JSON containing the translatableResources response.')
    return parser


def main() -> int:
    try:
        discover_existing(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
