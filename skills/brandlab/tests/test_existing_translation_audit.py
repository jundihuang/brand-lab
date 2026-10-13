import json
from pathlib import Path

from base import BrandLabTestCase
from shopify_case import FIXTURES, ShopifyModeTestCase


class ExistingTranslationAuditTest(ShopifyModeTestCase):
    def discover(self, workspace: Path, fixture: str) -> dict:
        self.run_cli(
            'shopify_discover_existing.py', '--workspace', str(workspace), '--market', 'IT',
            '--provider', 'fixture', '--fixture', str(FIXTURES / fixture), '--limit', '10',
        )
        return json.loads((workspace / 'shopify/existing-translations/IT-manifest.json').read_text())

    def records(self, workspace: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in (workspace / 'shopify/existing-translations/IT.jsonl').read_text().splitlines()
        ]

    def test_paired_snapshot_preserves_shopify_translation_fields(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        manifest = self.discover(workspace, 'existing-translations-paired.json')
        records = [json.loads(line) for line in (workspace / 'shopify/existing-translations/IT.jsonl').read_text().splitlines()]
        self.assertEqual(manifest['record_count'], 8)
        self.assertEqual(manifest['audit_mode'], 'paired_source')
        self.assertEqual(manifest['source_availability'], 'found')
        title = next(row for row in records if row['shopify_resource_id'].endswith('123') and row['field_key'] == 'title')
        self.assertEqual(title['audit_mode'], 'paired_source')
        self.assertEqual(title['source_text'], 'Leopard mini skirt')
        self.assertEqual(title['published_text'], 'Minigonna leopardo')
        self.assertEqual(title['source_digest'], 'digest-title')
        self.assertFalse(title['outdated'])

    def test_discovery_covers_high_frequency_resource_types(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.discover(workspace, 'existing-translations-paired.json')
        records = [json.loads(line) for line in (workspace / 'shopify/existing-translations/IT.jsonl').read_text().splitlines()]
        self.assertLessEqual({'product', 'collection', 'page', 'blog'}, {row['resource_type'] for row in records})
        self.assertIn('seo_title', {row['field_key'] for row in records})

    def test_paired_audit_flags_untranslated_and_recommends_retranslate(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.discover(workspace, 'existing-translations-paired.json')
        self.run_cli('audit_existing_translations.py', '--workspace', str(workspace), '--market', 'IT')
        report = json.loads((workspace / 'reports/existing-translation-audit-IT.json').read_text())
        silk = next(item for item in report['items'] if item['shopify_resource_id'].endswith('456'))
        self.assertEqual(silk['audit_mode'], 'paired_source')
        self.assertEqual(silk['status'], 'warning')
        self.assertEqual(silk['recommendation'], 'retranslate')
        self.assertIn('untranslated_source', {check['rule_id'] for check in silk['checks']})
        markdown = (workspace / 'reports/existing-translation-audit-IT.md').read_text()
        self.assertIn('Audit mode:', markdown)
        self.assertIn('Unverifiable checks:', markdown)

    def test_target_only_snapshot_does_not_claim_semantic_failures(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.discover(workspace, 'existing-translations-target-only.json')
        self.run_cli('audit_existing_translations.py', '--workspace', str(workspace), '--market', 'IT')
        report = json.loads((workspace / 'reports/existing-translation-audit-IT.json').read_text())
        item = report['items'][0]
        self.assertEqual(item['audit_mode'], 'target_only')
        self.assertEqual(item['status'], 'source_missing')
        self.assertIn('semantic_equivalence', item['skipped_checks'])
        self.assertNotIn('retranslate', [item['recommendation']])

    def test_uploaded_reference_upgrades_target_only_record(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        reference = workspace / 'source-references.json'
        reference.write_text(json.dumps([{
            'shopify_resource_id': 'gid://shopify/Product/789',
            'field_key': 'title',
            'source_locale': 'en',
            'source_text': 'Silk dress',
            'source_digest': 'digest-silk',
        }]), encoding='utf-8')
        self.run_cli(
            'shopify_discover_existing.py', '--workspace', str(workspace), '--market', 'IT',
            '--provider', 'fixture', '--fixture', str(FIXTURES / 'existing-translations-target-only.json'),
            '--reference-file', str(reference), '--limit', '10',
        )
        manifest = json.loads((workspace / 'shopify/existing-translations/IT-manifest.json').read_text())
        row = self.records(workspace)[0]
        self.assertEqual(manifest['audit_mode'], 'paired_source')
        self.assertEqual(manifest['source_availability'], 'found')
        self.assertEqual(row['source_origin'], 'uploaded_reference')
        self.assertEqual(row['source_text'], 'Silk dress')
        self.assertEqual(row['source_digest'], 'digest-silk')
        self.assertEqual(row['source_reference_digest'], 'digest-silk')

    def test_history_job_reference_upgrades_target_only_record(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        sources = workspace / 'jobs/history-job/sources'
        sources.mkdir(parents=True)
        (sources / 'item.json').write_text(json.dumps({
            'shopify_resource_id': 'gid://shopify/Product/789',
            'field_key': 'title',
            'source_available': True,
            'source': 'Silk dress',
            'source_digest': 'digest-history',
            'source_locale': 'en',
        }), encoding='utf-8')
        self.run_cli(
            'shopify_discover_existing.py', '--workspace', str(workspace), '--market', 'IT',
            '--provider', 'fixture', '--fixture', str(FIXTURES / 'existing-translations-target-only.json'),
            '--history-job', 'history-job', '--limit', '10',
        )
        row = self.records(workspace)[0]
        self.assertEqual(row['audit_mode'], 'paired_source')
        self.assertEqual(row['source_origin'], 'history_job')
        self.assertEqual(row['source_text'], 'Silk dress')
        self.assertEqual(row['source_digest'], 'digest-history')
        self.assertEqual(row['source_reference_digest'], 'digest-history')

    def test_resource_filter_limits_snapshot_without_touching_shopify(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        manifest = self.discover_with_args(workspace, ['--resource-type', 'product'])
        rows = self.records(workspace)
        self.assertEqual(manifest['resource_type_filter'], ['product'])
        self.assertEqual(manifest['record_count'], len(rows))
        self.assertEqual({row['resource_type'] for row in rows}, {'product'})

    def discover_with_args(self, workspace: Path, extra_args: list[str]) -> dict:
        self.run_cli(
            'shopify_discover_existing.py', '--workspace', str(workspace), '--market', 'IT',
            '--provider', 'fixture', '--fixture', str(FIXTURES / 'existing-translations-paired.json'),
            '--limit', '10', *extra_args,
        )
        return json.loads((workspace / 'shopify/existing-translations/IT-manifest.json').read_text())

    def audit(self, workspace: Path, extra_args: list[str] | None = None) -> dict:
        self.run_cli(
            'audit_existing_translations.py', '--workspace', str(workspace), '--market', 'IT',
            *(extra_args or []),
        )
        return json.loads((workspace / 'reports/existing-translation-audit-IT.json').read_text())

    def test_paired_ai_review_can_mark_semantic_failure(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.discover(workspace, 'existing-translations-paired.json')
        first = self.audit(workspace)['items'][0]
        review = workspace / 'ai-review.json'
        review.write_text(json.dumps({
            'reviews': [{
                'item_id': first['item_id'],
                'review_mode': 'paired_source',
                'model': 'test-model',
                'confidence': 0.94,
                'source_digest': first['source_digest'],
                'published_digest': first['published_digest'],
                'findings': [{
                    'type': 'mistranslation', 'severity': 'critical',
                    'message': 'The product material is translated incorrectly.',
                }],
                'suggested_text': 'Minigonna corretta',
                'recommendation': 'retranslate',
            }]
        }), encoding='utf-8')
        report = self.audit(workspace, ['--ai-review', str(review)])
        item = next(item for item in report['items'] if item['item_id'] == first['item_id'])
        self.assertEqual(report['ai_review_count'], 1)
        self.assertEqual(item['status'], 'fail')
        self.assertEqual(item['recommendation'], 'retranslate')
        self.assertEqual(item['ai_review']['model'], 'test-model')
        self.assertIn('ai_mistranslation', {check['rule_id'] for check in item['checks']})
        semantic = next(check for check in item['checks'] if check['rule_id'] == 'semantic_equivalence')
        self.assertEqual(semantic['status'], 'passed')

    def test_target_only_ai_review_lowers_confidence_and_stays_non_semantic(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.discover(workspace, 'existing-translations-target-only.json')
        first = self.audit(workspace)['items'][0]
        review = workspace / 'ai-review.json'
        review.write_text(json.dumps([{
            'item_id': first['item_id'],
            'review_mode': 'target_only',
            'confidence': 0.72,
            'published_digest': first['published_digest'],
            'findings': [{
                'type': 'clarity_issue', 'severity': 'warning',
                'message': 'The phrase is technically correct but unclear for shoppers.',
            }],
            'rewrite_suggestion': 'Vestido de seda para noche',
            'recommendation': 'rewrite',
        }]), encoding='utf-8')
        report = self.audit(workspace, ['--ai-review', str(review)])
        item = report['items'][0]
        self.assertEqual(report['ai_review_count'], 1)
        self.assertEqual(item['status'], 'warning')
        self.assertEqual(item['recommendation'], 'rewrite')
        self.assertEqual(item['ai_review']['rewrite_suggestion'], 'Vestido de seda para noche')
        self.assertIn('ai_clarity_issue', {check['rule_id'] for check in item['checks']})
        self.assertNotIn('semantic_equivalence', {check['rule_id'] for check in item['checks']})

    def test_target_only_ai_review_rejects_semantic_conclusion(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.discover(workspace, 'existing-translations-target-only.json')
        first = self.audit(workspace)['items'][0]
        review = workspace / 'ai-review.json'
        review.write_text(json.dumps([{
            'item_id': first['item_id'], 'review_mode': 'target_only', 'confidence': 0.7,
            'published_digest': first['published_digest'],
            'findings': [{'type': 'mistranslation', 'severity': 'critical', 'message': 'Invalid claim'}],
        }]), encoding='utf-8')
        process = self.run_cli(
            'audit_existing_translations.py', '--workspace', str(workspace), '--market', 'IT',
            '--ai-review', str(review), expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("type 'mistranslation' is not allowed for target_only", process.stderr)
