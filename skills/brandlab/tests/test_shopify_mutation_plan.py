import json
from pathlib import Path

import yaml

from brandlab_lib import BrandLabError
from shopify_case import ShopifyModeTestCase


class ShopifyMutationPlanTest(ShopifyModeTestCase):
    def test_approved_items_plan_includes_before_after_and_digests(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        manifest = self.prepare_approved_job(workspace)
        self.run_cli("build_mutation_plan.py", "--workspace", str(workspace), "--job", "m2-job", "--batch-id", "review-batch")
        plan = self.load_json(workspace / "shopify/mutation-plans/review-batch.json")
        self.assertEqual(plan["status"], "ready_for_review")
        self.assertEqual(plan["max_batch_size"], 10)
        self.assertEqual(len(plan["operations"]), 4)
        for operation in plan["operations"]:
            self.assertTrue(operation["before"])
            self.assertTrue(operation["after"])
            self.assertTrue(operation["source_digest"].startswith("sha256:"))
            self.assertTrue(operation["approval_digest"])
            self.assertEqual(operation["status"], "planned")
            self.assertEqual(operation["rollback_source"], operation["before"])
        self.assertTrue(plan["graphql"]["query"].startswith("mutation BrandLabControlledUpdate"))
        self.assertIn("userErrors", plan["graphql"]["query"])
        self.assertTrue((workspace / "shopify/mutation-plans/review-batch-review.md").is_file())
        self.assertIn("shopify_mutation_planned", self.history_events(workspace))

    def test_plan_rejects_items_before_translation_approval(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.prepare_approved_job(workspace)
        job_dir = workspace / "jobs/m2-job"
        manifest = self.load_json(job_dir / "manifest.json")
        for item in manifest["items"]:
            item["status"] = "translated"
        (job_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        process = self.run_cli(
            "build_mutation_plan.py", "--workspace", str(workspace), "--job", "m2-job", "--batch-id", "rejected",
            expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertFalse((workspace / "shopify/mutation-plans/rejected.json").exists())

    def test_batch_larger_than_configured_limit_is_rejected(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.prepare_approved_job(workspace)
        brand_path = workspace / "brand.yaml"
        brand = yaml.safe_load(brand_path.read_text())
        brand["shopify"]["max_batch_size"] = 2
        brand_path.write_text(yaml.safe_dump(brand, sort_keys=False, allow_unicode=True))
        process = self.run_cli(
            "build_mutation_plan.py", "--workspace", str(workspace), "--job", "m2-job", "--batch-id", "too-large",
            expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("exceed max_batch_size", process.stderr)

    def test_cancelled_plan_cannot_be_approved(self) -> None:
        workspace = self.temp_workspace()
        plan_path, plan, _ = self.prepare_approved_plan(workspace)
        plan["status"] = "cancelled"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        process = self.run_cli(
            "approve_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--reviewer", "Jayce",
            expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.load_json(plan_path)["status"], "cancelled")
