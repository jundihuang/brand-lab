import json
from pathlib import Path

from shopify_case import FIXTURES, ShopifyModeTestCase


class ShopifyExecutionTest(ShopifyModeTestCase):
    def test_plan_without_approval_cannot_execute(self) -> None:
        workspace = self.temp_workspace()
        plan_path, plan, manifest = self.prepare_approved_plan(workspace)
        process = self.run_cli(
            "execute_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--execute",
            "--provider", "fixture", "--fixture", str(FIXTURES / "mutation-success.json"), expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("ready_for_review", process.stderr)
        self.assertEqual(self.load_json(plan_path)["status"], "ready_for_review")
        self.assertEqual(self.load_json(workspace / "jobs/m2-job/manifest.json")["status"], "approved")

    def test_preflight_does_not_execute_or_change_statuses(self) -> None:
        workspace = self.temp_workspace()
        plan_path, plan, manifest = self.prepare_approved_plan(workspace, enable_mutations=True)
        self.run_cli(
            "approve_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--reviewer", "Jayce",
        )
        process = self.run_cli(
            "execute_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path),
            "--provider", "fixture", "--fixture", str(FIXTURES / "mutation-success.json"),
        )
        self.assertIn('"result": "preflight"', process.stdout)
        current_plan = self.load_json(plan_path)
        current_job = self.load_json(workspace / "jobs/m2-job/manifest.json")
        self.assertEqual(current_plan["status"], "approved")
        self.assertEqual(current_job["status"], "approved")
        self.assertEqual(len(current_plan["executions"]), 0)

    def test_successful_fixture_execution_and_verification_update_all_state(self) -> None:
        workspace = self.temp_workspace()
        plan_path, plan, _ = self.prepare_approved_plan(workspace, enable_mutations=True)
        self.run_cli(
            "approve_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--reviewer", "Jayce",
        )
        self.run_cli(
            "execute_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--execute",
            "--provider", "fixture", "--fixture", str(FIXTURES / "mutation-success.json"),
        )
        executed_plan = self.load_json(plan_path)
        result_path = workspace / executed_plan["executions"][-1]["result"]
        result = self.load_json(result_path)
        current_plan = self.load_json(plan_path)
        current_job = self.load_json(workspace / "jobs/m2-job/manifest.json")
        self.assertEqual(current_plan["status"], "executed")
        self.assertEqual(result["status"], "executed")
        self.assertEqual([item["status"] for item in result["items"]], ["success"] * 4)
        self.assertEqual([item["status"] for item in current_job["items"]], ["exported"] * 4)
        self.assertTrue((workspace / result["raw_output_path"]).is_file())
        command = [json.loads(line) for line in (workspace / "shopify/command-log.jsonl").read_text().splitlines()][-1]
        self.assertEqual(command["operation_kind"], "mutation")
        self.assertIn("shopify_execution_completed", self.history_events(workspace))

        self.run_cli(
            "verify_execution.py", "--workspace", str(workspace), "--result", str(result_path),
            "--provider", "fixture", "--fixture", str(FIXTURES / "readback-success.json"),
        )
        verified_result = self.load_json(result_path)
        verified_plan = self.load_json(plan_path)
        verified_job = self.load_json(workspace / "jobs/m2-job/manifest.json")
        self.assertEqual(verified_result["status"], "executed")
        self.assertEqual([item["status"] for item in verified_result["items"]], ["verified"] * 4)
        self.assertEqual(verified_job["status"], "approved")
        self.assertEqual([item["status"] for item in verified_job["items"]], ["verified"] * 4)
        self.assertEqual([operation["status"] for operation in verified_plan["operations"]], ["verified"] * 4)
        self.assertTrue(result_path.with_suffix(".verification.md").is_file())
        self.assertIn("shopify_execution_verified", self.history_events(workspace))

    def test_user_errors_fail_batch_block_items_and_prevent_retry(self) -> None:
        workspace = self.temp_workspace()
        plan_path, plan, _ = self.prepare_approved_plan(workspace, enable_mutations=True)
        self.run_cli(
            "approve_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--reviewer", "Jayce",
        )
        process = self.run_cli(
            "execute_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--execute",
            "--provider", "fixture", "--fixture", str(FIXTURES / "mutation-user-errors.json"), expect_success=False,
        )
        self.assertEqual(process.returncode, 1)
        execution = self.load_json(plan_path)
        job = self.load_json(workspace / "jobs/m2-job/manifest.json")
        self.assertEqual(execution["status"], "failed")
        self.assertEqual(job["status"], "failed")
        self.assertEqual([item["status"] for item in job["items"]], ["blocked"] * 4)
        retry = self.run_cli(
            "execute_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--execute",
            "--provider", "fixture", "--fixture", str(FIXTURES / "mutation-success.json"), expect_success=False,
        )
        self.assertEqual(retry.returncode, 2)
        self.assertEqual(len(self.load_json(plan_path)["executions"]), 1)
        self.assertIn("shopify_execution_failed", self.history_events(workspace))

    def test_verification_detects_readback_mismatch(self) -> None:
        workspace = self.temp_workspace()
        plan_path, plan, _ = self.prepare_approved_plan(workspace, enable_mutations=True)
        self.run_cli(
            "approve_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--reviewer", "Jayce",
        )
        self.run_cli(
            "execute_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--execute",
            "--provider", "fixture", "--fixture", str(FIXTURES / "mutation-success.json"),
        )
        executed_plan = self.load_json(plan_path)
        result_path = workspace / executed_plan["executions"][-1]["result"]
        self.run_cli(
            "verify_execution.py", "--workspace", str(workspace), "--result", str(result_path),
            "--provider", "fixture", "--fixture", str(FIXTURES / "readback-mismatch.json"),
        )
        result = self.load_json(result_path)
        job = self.load_json(workspace / "jobs/m2-job/manifest.json")
        current_plan = self.load_json(plan_path)
        self.assertEqual([item["status"] for item in result["items"]], ["mismatched", "verified", "verified", "verified"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(current_plan["operations"][0]["status"], "mismatched")
        self.assertIn("shopify_execution_verified", self.history_events(workspace))

    def test_fixture_execution_requires_enabled_mutations_and_existing_fixture(self) -> None:
        workspace = self.temp_workspace()
        plan_path, plan, _ = self.prepare_approved_plan(workspace, enable_mutations=False)
        self.run_cli(
            "approve_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--reviewer", "Jayce",
        )
        missing = self.run_cli(
            "execute_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--execute",
            "--provider", "fixture", "--fixture", "/tmp/brandlab-missing-fixture.json", expect_success=False,
        )
        self.assertEqual(missing.returncode, 2)
        disabled = self.run_cli(
            "execute_mutation_plan.py", "--workspace", str(workspace), "--plan", str(plan_path), "--execute",
            "--provider", "fixture", "--fixture", str(FIXTURES / "mutation-success.json"), expect_success=False,
        )
        self.assertEqual(disabled.returncode, 2)
        self.assertIn("allow_mutations is false", disabled.stderr)
        self.assertEqual(self.load_json(plan_path)["status"], "approved")
