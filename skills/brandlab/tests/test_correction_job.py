import json
from pathlib import Path

from base import BrandLabTestCase
from shopify_case import FIXTURES, ShopifyModeTestCase


class CorrectionJobTest(ShopifyModeTestCase):
    def audit_market(self, workspace: Path, fixture: str) -> dict:
        self.run_cli(
            "shopify_discover_existing.py", "--workspace", str(workspace), "--market", "IT",
            "--provider", "fixture", "--fixture", str(FIXTURES / fixture), "--limit", "10",
        )
        self.run_cli("audit_existing_translations.py", "--workspace", str(workspace), "--market", "IT")
        return json.loads((workspace / "reports/existing-translation-audit-it.json").read_text())

    def prepare_approved_correction(self, workspace: Path, job_id: str, item_id: str, text: str) -> None:
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", job_id)
        snapshot = json.loads((workspace / "jobs" / job_id / "sources" / f"{item_id}.json").read_text())
        output = [{
            "item_id": item_id, "translated_text": text, "source_digest": snapshot["source_digest"],
            "source_locale": snapshot["source_locale"], "target_locale": snapshot["target_locale"],
        }]
        (workspace / "jobs" / job_id / "model-translations.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output), encoding="utf-8"
        )
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", job_id, "--input", "model-translations.jsonl")
        self.run_cli("build_review.py", "--workspace", str(workspace), "--job", job_id)
        self.run_cli(
            "apply_approval.py", "--workspace", str(workspace), "--job", job_id,
            "--decision", "approve", "--item-id", "all", "--reviewer", "Jayce",
        )

    def test_paired_retranslation_goes_through_review_and_approval(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        report = self.audit_market(workspace, "existing-translations-paired.json")
        item = next(row for row in report["items"] if row["recommendation"] == "retranslate")
        self.run_cli(
            "create_correction_job.py", "--workspace", str(workspace), "--market", "IT",
            "--ids", item["item_id"], "--job-id", "correction-paired",
        )
        manifest = json.loads((workspace / "jobs/correction-paired/manifest.json").read_text())
        self.assertEqual(manifest["job_type"], "market_translation_correction")
        self.assertEqual(manifest["correction_mode"], "retranslate")
        self.assertEqual(manifest["source_locale"], "en")
        self.assertTrue(manifest["semantic_validation_available"])
        self.prepare_approved_correction(workspace, "correction-paired", item["item_id"], "Minigonna di seta")
        approved = json.loads((workspace / "jobs/correction-paired/manifest.json").read_text())
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["items"][0]["status"], "approved")

    def test_target_only_rewrite_does_not_require_source(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        report = self.audit_market(workspace, "existing-translations-target-only.json")
        item = report["items"][0]
        self.assertEqual(item["audit_mode"], "target_only")
        self.assertIsNone(item["source_locale"])
        self.run_cli(
            "create_correction_job.py", "--workspace", str(workspace), "--market", "IT",
            "--ids", item["item_id"], "--mode", "rewrite", "--job-id", "correction-rewrite",
        )
        manifest = json.loads((workspace / "jobs/correction-rewrite/manifest.json").read_text())
        snapshot = json.loads((workspace / "jobs/correction-rewrite/sources" / f"{item['item_id']}.json").read_text())
        self.assertEqual(manifest["correction_mode"], "rewrite")
        self.assertFalse(manifest["semantic_validation_available"])
        self.assertFalse(snapshot["source_available"])
        self.assertEqual(snapshot["published_text"], "Vestido de seda premium")
        self.prepare_approved_correction(workspace, "correction-rewrite", item["item_id"], "Minigonna di seta premium")
        approved = json.loads((workspace / "jobs/correction-rewrite/manifest.json").read_text())
        self.assertEqual(approved["status"], "approved")
        audit = json.loads((workspace / "jobs/correction-rewrite/audits" / f"{item['item_id']}.audit.json").read_text())
        self.assertFalse(audit["semantic_validation_available"])
