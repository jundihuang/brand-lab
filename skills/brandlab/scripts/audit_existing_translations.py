#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, extract_tags, find_market,
    glossary_for_locale, load_brand, load_guidelines, make_check, match_guidelines,
    normalize_term, read_glossary, resolve_workspace, rule_values, slug, utc_now,
)

PAIRED_ONLY_SKIPPED = [
    'ai_semantic_equivalence', 'ai_omission', 'ai_addition', 'ai_mistranslation',
]
TARGET_ONLY_SKIPPED = [
    'semantic_equivalence', 'omission', 'addition', 'mistranslation',
    'placeholder_parity', 'html_tag_parity', 'untranslated_source',
]
LANGUAGE_MARKERS = {
    'en': {'the', 'and', 'with', 'from', 'made', 'for'},
    'it': {'il', 'lo', 'la', 'con', 'di', 'per', 'una', 'realizzata'},
    'fr': {'le', 'la', 'les', 'avec', 'pour', 'une', 'fabriquée'},
    'es': {'el', 'la', 'los', 'con', 'para', 'una', 'hecha'},
    'de': {'der', 'die', 'das', 'mit', 'für', 'aus', 'gefertigt'},
}


def balanced_placeholders(value: str) -> bool:
    curly = value.count('{') == value.count('}') and value.count('{') % 2 == 0
    percent = not re.search(r'%[sdif]', value)
    return curly and percent


def language_check(text: str, target_locale: str) -> tuple[Any, str]:
    base = target_locale.split('-', 1)[0].casefold()
    words = set(re.findall(r'[a-z]+', text.casefold()))
    expected = LANGUAGE_MARKERS.get(base, set())
    english_hits = len(words & LANGUAGE_MARKERS['en'])
    if expected and not (words & expected) and english_hits >= 2:
        return make_check('language_quality', 'warning', 'soft', 'Text appears to remain in English instead of the target language.'), 'warning'
    return make_check('language_quality', 'passed', 'soft'), 'passed'


def guideline_checks(text: str, guidelines: list[dict[str, Any]], target_locale: str) -> list[Any]:
    checks: list[Any] = []
    for guideline, rule in rule_values(guidelines, 'forbidden_regex'):
        try:
            if re.search(str(rule['pattern']), text, flags=re.I):
                checks.append(make_check(f"forbidden_regex:{guideline['id']}", 'failed', 'hard', f"Forbidden expression matched rule '{rule['pattern']}'."))
        except re.error as exc:
            checks.append(make_check(f"forbidden_regex:{guideline['id']}", 'warning', 'soft', f'Guideline regex is invalid: {exc}.'))
    for guideline, rule in rule_values(guidelines, 'required_regex'):
        try:
            if not re.search(str(rule['pattern']), text, flags=re.I):
                checks.append(make_check(f"required_regex:{guideline['id']}", 'warning', 'soft', 'Required expression is missing.'))
        except re.error as exc:
            checks.append(make_check(f"required_regex:{guideline['id']}", 'warning', 'soft', f'Guideline regex is invalid: {exc}.'))
    for guideline, rule in rule_values(guidelines, 'max_length'):
        limit = rule.get('value')
        if isinstance(limit, int) and len(text) > limit:
            checks.append(make_check(f"max_length:{guideline['id']}", 'warning', 'hard', f'Text length {len(text)} exceeds {limit}.'))
    return checks


def glossary_checks(text: str, glossary: list[dict[str, str]], target_locale: str, paired: bool = True) -> list[Any]:
    checks: list[Any] = []
    if not paired:
        return [make_check('brand_terminology', 'passed', 'hard')]
    for row in glossary_for_locale(glossary, target_locale):
        if row.get('status') != 'locked':
            continue
        if normalize_term(row.get('target_term', '')) in normalize_term(text):
            checks.append(make_check('brand_terminology', 'passed', 'hard'))
        else:
            checks.append(make_check('brand_terminology', 'warning', 'hard', f"Locked target term '{row.get('target_term')}' is missing."))
    if not checks:
        checks.append(make_check('brand_terminology', 'passed', 'hard'))
    return checks


def audit_one(row: dict[str, Any], market_entry: dict[str, Any], guidelines: list[dict[str, Any]], glossary: list[dict[str, str]]) -> dict[str, Any]:
    paired = row.get('audit_mode') == 'paired_source'
    source = str(row.get('source_text') or '')
    published = str(row.get('published_text') or '')
    checks: list[Any] = []
    if not row.get('source_available'):
        if not published.strip():
            return {
                **base_result(row, market_entry),
                'status': 'missing', 'recommendation': 'rewrite', 'trust_score': 0,
                'checks': [make_check('existing_translation', 'warning', 'hard', 'No published translation was found.')],
                'skipped_checks': TARGET_ONLY_SKIPPED + PAIRED_ONLY_SKIPPED,
            }
        checks.append(make_check('source_reference', 'warning', 'soft', 'No source reference was available; semantic equivalence cannot be verified.'))
    else:
        if not published.strip():
            return {
                **base_result(row, market_entry),
                'status': 'missing', 'recommendation': 'retranslate', 'trust_score': 0,
                'checks': [make_check('existing_translation', 'warning', 'hard', 'No published translation was found.')],
                'skipped_checks': [],
            }
        checks.append(make_check('source_reference', 'passed', 'soft'))

    if not published.strip():
        checks.append(make_check('empty_content', 'failed', 'hard', 'Published translation is empty.'))
    else:
        checks.append(make_check('empty_content', 'passed', 'hard'))

    language, _ = language_check(published, row.get('target_locale') or market_entry['locale'])
    checks.append(language)
    if not balanced_placeholders(published):
        checks.append(make_check('placeholder_syntax', 'failed', 'hard', 'Placeholder syntax is unbalanced.'))
    else:
        checks.append(make_check('placeholder_syntax', 'passed', 'hard'))

    expected_tags = Counter(extract_tags(source))
    actual_tags = Counter(extract_tags(published))
    if paired:
        if expected_tags == actual_tags:
            checks.append(make_check('html_tag_parity', 'passed', 'hard'))
        else:
            checks.append(make_check('html_tag_parity', 'failed', 'hard', 'HTML tags differ from the source reference.'))
        normalized_source = re.sub(r'\s+', ' ', source).strip().casefold()
        normalized_target = re.sub(r'\s+', ' ', published).strip().casefold()
        if normalized_source == normalized_target and len(normalized_source) > 8:
            checks.append(make_check('untranslated_source', 'warning', 'hard', 'Published text is identical to the source reference.'))
        else:
            checks.append(make_check('untranslated_source', 'passed', 'hard'))
        checks.append(make_check('semantic_equivalence', 'warning', 'soft', 'Deterministic audit only; run optional AI Review for semantic equivalence.'))
    else:
        opening = sum(actual_tags.values())
        expected_opening = sum(expected_tags.values())
        checks.append(make_check('html_syntax', 'passed' if opening == expected_opening else 'warning', 'soft', '' if opening == expected_opening else 'HTML quantity differs from available context; inspect manually.'))

    checks.extend(guideline_checks(published, guidelines, row.get('target_locale') or market_entry['locale']))
    checks.extend(glossary_checks(published, glossary, row.get('target_locale') or market_entry['locale'], paired))
    if row.get('field_key', '').startswith('seo'):
        if len(published) > 70:
            checks.append(make_check('seo_length', 'warning', 'soft', 'SEO text is longer than 70 characters.'))
        else:
            checks.append(make_check('seo_length', 'passed', 'soft'))

    failed = [check for check in checks if check['status'] == 'failed']
    warnings = [check for check in checks if check['status'] == 'warning']
    hard_failures = [check for check in failed if check['severity'] == 'hard']
    if hard_failures:
        status = 'fail'
        recommendation = 'retranslate' if paired else 'rewrite'
    elif warnings:
        non_source_warnings = [check for check in warnings if check['rule_id'] != 'source_reference']
        if paired:
            status = 'warning'
            if any(check['rule_id'] == 'untranslated_source' for check in warnings):
                recommendation = 'retranslate'
            elif any(check['rule_id'] not in {'semantic_equivalence', 'source_reference'} for check in warnings):
                recommendation = 'fix'
            else:
                recommendation = 'manual_review'
        elif non_source_warnings:
            status = 'warning'
            recommendation = 'manual_review'
        else:
            status = 'source_missing'
            recommendation = 'add_reference'
    else:
        status = 'pass' if paired else 'source_missing'
        recommendation = 'keep' if paired else 'add_reference'
    trust_score = 100 if status == 'pass' else 70 if status == 'warning' else 30
    if paired and status == 'fail':
        trust_score = 40
    return {
        **base_result(row, market_entry), 'status': status, 'recommendation': recommendation,
        'trust_score': trust_score, 'checks': checks,
        'skipped_checks': [] if paired else TARGET_ONLY_SKIPPED,
    }


def base_result(row: dict[str, Any], market_entry: dict[str, Any]) -> dict[str, Any]:
    paired = row.get('audit_mode') == 'paired_source'
    return {
        'item_id': slug(str(row.get('record_id') or f"{row.get('shopify_resource_id')}:{row.get('field_key')}")),
        'audit_mode': 'paired_source' if paired else 'target_only',
        'market': str(market_entry.get('id') or market_entry['market']),
        'target_locale': row.get('target_locale') or market_entry['locale'],
        'resource_type': row.get('resource_type', ''),
        'shopify_resource_id': row.get('shopify_resource_id', ''),
        'field_key': row.get('field_key', ''),
        'source_available': row.get('source_available') is True,
        'source_origin': row.get('source_origin') or ('shopify_base' if paired else 'none'),
        'source_locale': row.get('source_locale') if paired else None,
        'source_text': row.get('source_text') or '',
        'source_digest': row.get('source_digest'),
        'translatable_content_digest': row.get('source_digest') or row.get('published_digest'),
        'published_text': row.get('published_text') or '',
        'published_digest': row.get('published_digest'),
        'unverifiable_checks': [] if paired else TARGET_ONLY_SKIPPED,
        'ai_review': None,
    }


def audit(args: argparse.Namespace) -> None:
    workspace = resolve_workspace(Path(args.workspace))
    brand = load_brand(workspace)
    market_entry = find_market(brand, args.market)
    market_id = str(market_entry.get('id') or market_entry['market'])
    market_slug = slug(market_id)
    snapshot_path = Path(args.snapshot).expanduser().resolve() if args.snapshot else workspace / 'shopify' / 'existing-translations' / f'{market_slug}.jsonl'
    if not snapshot_path.is_file():
        absolute = snapshot_path if snapshot_path.is_absolute() else workspace / snapshot_path
        raise BrandLabError(f'{snapshot_path}: existing translation snapshot is missing. Run shopify_discover_existing.py first.')
    from brandlab_lib import read_jsonl
    rows = read_jsonl(snapshot_path)
    if not rows:
        raise BrandLabError(f'{snapshot_path}: snapshot is empty. Re-run existing translation discovery.')

    guideline_items = load_guidelines(workspace)
    guideline_filter = {
        'resource_type': (args.resource_type or ['*'])[0],
        'target_locale': market_entry['locale'],
        'market': market_id,
    }
    guidelines = [guideline for _, guideline in match_guidelines(guideline_items, guideline_filter)]
    glossary = read_glossary(workspace / 'glossary' / 'glossary.csv')
    results = [audit_one(row, market_entry, guidelines, glossary) for row in rows]
    counts = Counter(item['status'] for item in results)
    audit_id = f'existing-audit-{market_slug}-{utc_now()[:10]}'
    report_doc = {
        'audit_id': audit_id, 'created_at': utc_now(), 'market': market_id,
        'target_locale': market_entry['locale'], 'snapshot_path': str(snapshot_path),
        'audit_mode': 'paired_source' if all(item['audit_mode'] == 'paired_source' for item in results) else ('target_only' if all(item['audit_mode'] == 'target_only' for item in results) else 'mixed'),
        'source_availability': 'found' if all(item['source_available'] for item in results) else ('not_found' if not any(item['source_available'] for item in results) else 'mixed'),
        'counts': dict(counts), 'items': results,
    }
    report_json = workspace / 'reports' / f'existing-translation-audit-{market_slug}.json'
    atomic_write_json(report_json, report_doc)

    origins = sorted({str(item['source_origin']) for item in results if item['source_origin']})
    unverifiable = sorted({check for item in results for check in item['unverifiable_checks']})
    lines = [
        '# Existing Translation Audit', '',
        f'- Audit ID: `{audit_id}`', f'- Market: `{market_id}`',
        f"- Audit mode: `{report_doc['audit_mode']}`",
        f"- Source availability: `{report_doc['source_availability']}`",
        f"- Source origin: `{', '.join(origins) or 'none'}`",
        f"- Unverifiable checks: `{', '.join(unverifiable) or 'none'}`", '',
        '| Item | Resource | Field | Status | Recommendation | Trust |', '|---|---|---|---|---|---:|',
    ]
    for item in results:
        lines.append(f"| `{item['item_id']}` | `{item['resource_type']}` | `{item['field_key']}` | {item['status']} | {item['recommendation']} | {item['trust_score']} |")
    lines += ['', '## Next action', '']
    lines.append('Review warnings and failures, then request a correction plan. This audit did not modify Shopify.')
    report_md = workspace / 'reports' / f'existing-translation-audit-{market_slug}.md'
    report_md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    append_history(workspace, 'existing_translation_audited', {
        'audit_id': audit_id, 'market': market_id, 'items': len(results), 'counts': dict(counts),
    })
    print(json.dumps({'result': 'ok', 'audit_id': audit_id, 'market': market_id, 'counts': dict(counts), 'report': str(report_md)}, ensure_ascii=False))
    print('Next: confirm which audit items should become corrections; do not write to Shopify yet.')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Audit an existing Shopify translation snapshot without changing Shopify.')
    parser.add_argument('--workspace', default='brandlab')
    parser.add_argument('--market', required=True)
    parser.add_argument('--snapshot', help='Optional JSONL snapshot path; defaults to the latest market snapshot.')
    parser.add_argument('--resource-type', nargs='*', help='Optional guideline resource-type filter.')
    return parser


def main() -> int:
    try:
        audit(build_parser().parse_args())
        return 0
    except BrandLabError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
