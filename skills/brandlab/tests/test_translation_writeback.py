import json
from pathlib import Path

from shopify_case import FIXTURES, ShopifyModeTestCase


class TranslationWritebackTest(ShopifyModeTestCase):
    def prepare_approved_correction(self, workspace: Path) -> str:
        self.run_cli(
            "shopify_discover_existing.py", "--workspace", str(workspace), "--market", "IT",
            "--provider", "fixture", "--fixture", str(FIXTURES / "existing-translations-paired.json"),
            "--limit", "10",
        )
        self.run_cli("audit_existing_translations.py", "--workspace", str(workspace), "--market", "IT")
        report = self.load_json(workspace / "reports/existing-translation-audit-it.json")
        item = next(row for row in report["items"] if row["recommendation"] == "retranslate")
        self.run_cli(
            "create_correction_job.py", "--workspace", str(workspace), "--market", "IT",
            "--ids", item["item_id"], "--mode", "retranslate", "--job-id", "writeback-job",
        )
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", "writeback-job")
        snapshot = self.load_json(workspace / "jobs/writeback-job/sources" / f"{item['item_id']}.json")
        output = [{
            "item_id": item["item_id"], "translated_text": "Minigonna di seta", "source_digest": snapshot["source_digest"],
            "source_locale": snapshot["source_locale"], "target_locale": snapshot["target_locale"],
        }]
        (workspace / "jobs/writeback-job/model-translations.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output), encoding="utf-8"
        )
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", "writeback-job", "--input", "model-translations.jsonl")
        self.run_cli("build_review.py", "--workspace", str(workspace), "--job", "writeback-job")
        self.run_cli(
            "apply_approval.py", "--workspace", str(workspace), "--job", "writeback-job",
            "--decision", "approve", "--item-id", "all", "--reviewer", "Jayce",
        )
        return item["item_id"]

    def test_translation_register_plan_executes_and_verifies_market_translation(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace, enable_mutations=True)
        item_id = self.prepare_approved_correction(workspace)
        plan_path = workspace / "shopify/translation-writeback-plans/translation-batch.json"
        self.run_cli(
            "build_translation_writeback_plan.py", "--workspace", str(workspace), "--job", "writeback-job",
            "--ids", item_id, "--batch-id", "translation-batch", "--shopify-market-id", "gid://shopify/Market/US",
            "--provider", "fixture", "--fixture", str(FIXTURES / "setup-ok.json"),
        )
        plan = self.load_json(plan_path)
        self.assertEqual(plan["plan_type"], "translation_register")
        self.assertEqual(plan["shopify_locale"], "it")
        self.assertIn("translationsRegister", plan["graphql"]["query"])
        self.assertEqual(plan["operations"][0]["translatable_content_digest"], "digest-silk")
        self.run_cli(
            "approve_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--reviewer", "Jayce",
        )
        preflight = self.run_cli(
            "execute_translation_writeback.py", "--workspace", str(workspace), "--plan", str(plan_path),
            "--provider", "fixture", "--digest-fixture", str(FIXTURES / "translation-digest-ok.json"),
        )
        self.assertIn('"result": "preflight"', preflight.stdout)
        self.assertFalse((workspace / "shopify/translation-writeback-results").exists())
        self.run_cli(
            "execute_translation_writeback.py", "--workspace", str(workspace), "--plan", str(plan_path), "--execute",
            "--provider", "fixture", "--digest-fixture", str(FIXTURES / "translation-digest-ok.json"),
            "--fixture", str(FIXTURES / "translation-register-ok.json"),
        )
        result_files = list((workspace / "shopify/translation-writeback-results").glob("*.json"))
        self.assertEqual(len(result_files), 1)
        self.run_cli(
            "verify_translation_writeback.py", "--workspace", str(workspace), "--result", str(result_files[0]),
            "--provider", "fixture", "--fixture", str(FIXTURES / "translation-readback-ok.json"),
        )
        result = self.load_json(result_files[0])
        plan = self.load_json(plan_path)
        manifest = self.load_json(workspace / "jobs/writeback-job/manifest.json")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(plan["operations"][0]["status"], "verified")
        self.assertEqual(manifest["items"][0]["writeback_status"], "verified")
        self.run_cli("build_market_summary.py", "--workspace", str(workspace))
        summary = self.load_json(workspace / "reports/market-translation-summary.json")
        market = next(row for row in summary["markets"] if row["market"] == "IT")
        self.assertEqual(market["audit"]["counts"]["warning"], 8)
        self.assertEqual(market["jobs"][0]["writeback_statuses"]["verified"], 1)
