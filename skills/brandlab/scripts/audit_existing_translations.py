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
    atomic_write_text,
    BrandLabError, append_history, atomic_write_json, extract_tags, find_market,
    glossary_for_locale, load_brand, load_guidelines, make_check, match_guidelines,
    normalize_term, read_glossary, read_jsonl_contract, resolve_workspace, rule_values, slug, utc_now, validate_schema,
)

PAIRED_ONLY_SKIPPED = [
    'ai_semantic_equivalence', 'ai_omission', 'ai_addition', 'ai_mistranslation',
]
TARGET_ONLY_SKIPPED = [
    'semantic_equivalence', 'omission', 'addition', 'mistranslation',
    'placeholder_parity', 'html_tag_parity', 'untranslated_source',
]
PAIRED_AI_FINDINGS = {
    'mistranslation', 'omission', 'addition', 'tone_mismatch',
    'brand_term_issue', 'seo_issue',
}
TARGET_ONLY_AI_FINDINGS = {
    'grammar_issue', 'clarity_issue', 'tone_mismatch', 'brand_term_issue',
    'locale_issue', 'seo_issue', 'compliance_issue',
}
AI_RECOMMENDATIONS = {
    'keep', 'fix', 'retranslate', 'rewrite', 'localize', 'manual_review', 'add_reference',
}
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


def load_ai_reviews(path: Path, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from brandlab_lib import read_json, read_jsonl
    if path.suffix.casefold() == '.jsonl':
        values = read_jsonl(path)
    else:
        value = read_json(path)
        values = value.get('reviews', []) if isinstance(value, dict) else value
        if not isinstance(values, list):
            raise BrandLabError(f"{path}: AI review file must contain an array or a reviews array.")

    def review_item_id(item: dict[str, Any]) -> str:
        return slug(str(item.get('record_id') or f"{item.get('shopify_resource_id')}:{item.get('field_key')}"))

    item_ids = {review_item_id(row) for row in rows}
    result: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values, 1):
        if not isinstance(value, dict) or not value.get('item_id'):
            raise BrandLabError(f"{path}: review {index} needs item_id.")
        item_id = str(value['item_id'])
        if item_id not in item_ids:
            raise BrandLabError(f"{path}: review {index} refers to unknown audit item '{item_id}'.")
        if item_id in result:
            raise BrandLabError(f"{path}: review {index} duplicates item '{item_id}'.")
        row = next(item for item in rows if review_item_id(item) == item_id)
        result[item_id] = normalize_ai_review(value, row, path, index)
    return result


def normalize_ai_review(value: dict[str, Any], row: dict[str, Any], path: Path, index: int) -> dict[str, Any]:
    paired = row.get('audit_mode') == 'paired_source'
    review_mode = 'paired_source' if paired else 'target_only'
    if value.get('review_mode') not in {'paired_source', 'target_only'}:
        raise BrandLabError(f"{path}: review {index} needs review_mode='paired_source' or 'target_only'.")
    if value['review_mode'] != review_mode:
        raise BrandLabError(
            f"{path}: review {index} declares {value['review_mode']} but audit item {value['item_id']} is {review_mode}."
        )
    try:
        confidence = float(value.get('confidence'))
    except (TypeError, ValueError) as exc:
        raise BrandLabError(f"{path}: review {index} needs confidence between 0 and 1.") from exc
    if not 0 <= confidence <= 1:
        raise BrandLabError(f"{path}: review {index} needs confidence between 0 and 1.")
    if not paired and confidence > 0.8:
        raise BrandLabError(f"{path}: review {index} target-only confidence must be 0.8 or lower.")
    if not str(row.get('published_text') or '').strip():
        raise BrandLabError(f"{path}: review {index} cannot review a missing published translation.")

    expected_published_digest = row.get('published_digest')
    if value.get('published_digest') != expected_published_digest:
        raise BrandLabError(
            f"{path}: review {index} published_digest does not match audit item {value['item_id']}. "
            "Generate AI Review from the current audit report."
        )
    if paired and value.get('source_digest') != row.get('source_digest'):
        raise BrandLabError(
            f"{path}: review {index} source_digest does not match audit item {value['item_id']}. "
            "Generate AI Review from the current audit report."
        )

    findings = value.get('findings')
    if not isinstance(findings, list):
        raise BrandLabError(f"{path}: review {index} needs a findings array.")
    allowed = PAIRED_AI_FINDINGS if paired else TARGET_ONLY_AI_FINDINGS
    normalized_findings = []
    for finding_no, finding in enumerate(findings, 1):
        if not isinstance(finding, dict):
            raise BrandLabError(f"{path}: review {index} finding {finding_no} must be an object.")
        finding_type = str(finding.get('type') or '')
        if finding_type not in allowed:
            raise BrandLabError(
                f"{path}: review {index} finding {finding_no} type '{finding_type or '(missing)'}' "
                f"is not allowed for {review_mode}."
            )
        severity = finding.get('severity')
        if severity not in {'warning', 'critical'}:
            raise BrandLabError(f"{path}: review {index} finding {finding_no} severity must be warning or critical.")
        normalized_findings.append({
            'type': finding_type, 'severity': severity,
            'message': str(finding.get('message') or ''),
        })

    recommendation = value.get('recommendation')
    if recommendation is not None and recommendation not in AI_RECOMMENDATIONS:
        raise BrandLabError(f"{path}: review {index} has an unsupported recommendation.")
    if not paired and recommendation in {'fix', 'retranslate'}:
        raise BrandLabError(f"{path}: review {index} cannot recommend source-based correction in target-only mode.")
    if paired and recommendation == 'rewrite':
        raise BrandLabError(f"{path}: review {index} cannot recommend rewrite in paired-source mode.")

    suggestion = str(value.get('suggested_text') or value.get('rewrite_suggestion') or '')
    normalized = {
        'review_mode': review_mode,
        'model': str(value.get('model') or 'external_ai_review'),
        'confidence': confidence,
        'findings': normalized_findings,
        'recommendation': recommendation,
        'published_digest': expected_published_digest,
    }
    if paired:
        if not suggestion and any(finding['type'] in {'mistranslation', 'omission', 'addition'} for finding in normalized_findings):
            raise BrandLabError(f"{path}: review {index} needs suggested_text for a semantic correction.")
        normalized['source_digest'] = row.get('source_digest')
        normalized['suggested_text'] = suggestion
    else:
        normalized['rewrite_suggestion'] = suggestion
    return normalized


def audit_one(
    row: dict[str, Any], market_entry: dict[str, Any], guidelines: list[dict[str, Any]],
    glossary: list[dict[str, str]], ai_review: dict[str, Any] | None = None,
    audit_policy: str = "standard",
) -> dict[str, Any]:
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
    if audit_policy == 'seo_and_legal' and paired:
        seo_fields = {'meta_title', 'meta_description', 'seo_title', 'seo_description'}
        if str(row.get('field_key') or '') in seo_fields:
            if len(published.strip()) > 160:
                checks.append(make_check('seo_length', 'warning', 'soft', f'SEO content exceeds 160 chars ({len(published.strip())}).'))
            else:
                checks.append(make_check('seo_length', 'passed', 'soft'))
        legal_terms = {'patent', 'trademark', 'copyright', 'warranty', 'liability'}
        for term in legal_terms:
            if term in source.lower() and term not in published.lower():
                checks.append(make_check('legal_term_parity', 'warning', 'soft', f'Legal term "{term}" in source is missing from published.'))
    if paired:
        if ai_review:
            checks.append(make_check('semantic_equivalence', 'passed', 'soft', 'Checked by optional AI Review.'))
        else:
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

    if ai_review:
        for finding in ai_review['findings']:
            checks.append(make_check(
                f"ai_{finding['type']}",
                'failed' if finding['severity'] == 'critical' else 'warning',
                'hard' if finding['severity'] == 'critical' else 'soft',
                finding['message'],
            ))

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
    if ai_review and ai_review.get('findings') and ai_review.get('recommendation') and status in {'warning', 'fail'}:
        recommendation = ai_review['recommendation']
    trust_score = 100 if status == 'pass' else 70 if status == 'warning' else 30
    if paired and status == 'fail':
        trust_score = 40
    return {
        **base_result(row, market_entry), 'status': status, 'recommendation': recommendation,
        'trust_score': trust_score, 'checks': checks,
        'skipped_checks': [] if paired else TARGET_ONLY_SKIPPED,
        'ai_review': ai_review,
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
        'translatable_content_digest': row.get('translatable_content_digest') or row.get('source_digest') or row.get('published_digest'),
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
    rows = read_jsonl_contract(snapshot_path, "existing-translation")
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
    ai_review_path = Path(args.ai_review).expanduser().resolve() if args.ai_review else None
    ai_reviews = load_ai_reviews(ai_review_path, rows) if ai_review_path else {}
    audit_policy = str(market_entry.get('audit_policy') or 'standard')
    results = [
        audit_one(row, market_entry, guidelines, glossary, ai_reviews.get(
            slug(str(row.get('record_id') or f"{row.get('shopify_resource_id')}:{row.get('field_key')}"))
        ), audit_policy=audit_policy)
        for row in rows
    ]
    counts = Counter(item['status'] for item in results)
    audit_id = f'existing-audit-{market_slug}-{utc_now()[:10]}'
    report_doc = {
        'audit_id': audit_id, 'created_at': utc_now(), 'market': market_id,
        'target_locale': market_entry['locale'], 'snapshot_path': str(snapshot_path),
        'audit_mode': 'paired_source' if all(item['audit_mode'] == 'paired_source' for item in results) else ('target_only' if all(item['audit_mode'] == 'target_only' for item in results) else 'mixed'),
        'source_availability': 'found' if all(item['source_available'] for item in results) else ('not_found' if not any(item['source_available'] for item in results) else 'mixed'),
        'counts': dict(counts), 'items': results,
        'ai_review_file': str(ai_review_path) if ai_review_path else None,
        'ai_review_count': sum(item['ai_review'] is not None for item in results),
    }
    report_json = workspace / 'reports' / f'existing-translation-audit-{market_slug}.json'
    validate_schema("existing-translation-audit", report_doc, report_json)
    atomic_write_json(report_json, report_doc)

    origins = sorted({str(item['source_origin']) for item in results if item['source_origin']})
    unverifiable = sorted({check for item in results for check in item['unverifiable_checks']})
    lines = [
        '# Existing Translation Audit', '',
        f'- Audit ID: `{audit_id}`', f'- Market: `{market_id}`',
        f"- Audit mode: `{report_doc['audit_mode']}`",
        f"- Source availability: `{report_doc['source_availability']}`",
        f"- Source origin: `{', '.join(origins) or 'none'}`",
        f"- AI reviewed items: `{sum(item['ai_review'] is not None for item in results)}`",
        f"- Unverifiable checks: `{', '.join(unverifiable) or 'none'}`", '',
        '| Item | Resource | Field | Status | Recommendation | Trust |', '|---|---|---|---|---|---:|',
    ]
    for item in results:
        lines.append(f"| `{item['item_id']}` | `{item['resource_type']}` | `{item['field_key']}` | {item['status']} | {item['recommendation']} | {item['trust_score']} |")
    lines += ['', '## Next action', '']
    lines.append('Review warnings and failures, then request a correction plan. This audit did not modify Shopify.')
    report_md = workspace / 'reports' / f'existing-translation-audit-{market_slug}.md'
    atomic_write_text(report_md, '\n'.join(lines) + '\n')
    append_history(workspace, 'existing_translation_audited', {
        'audit_id': audit_id, 'market': market_id, 'items': len(results), 'counts': dict(counts),
        'ai_reviewed': sum(item['ai_review'] is not None for item in results),
    })
    print(json.dumps({'result': 'ok', 'audit_id': audit_id, 'market': market_id, 'counts': dict(counts), 'report': str(report_md)}, ensure_ascii=False))
    print('Next: confirm which audit items should become corrections; do not write to Shopify yet.')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Audit an existing Shopify translation snapshot without changing Shopify.')
    parser.add_argument('--workspace', default='brandlab')
    parser.add_argument('--market', required=True)
    parser.add_argument('--snapshot', help='Optional JSONL snapshot path; defaults to the latest market snapshot.')
    parser.add_argument('--ai-review', help='Optional JSON or JSONL AI review file matched by item ID and digest.')
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
