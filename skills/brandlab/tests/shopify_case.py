from __future__ import annotations

import json
from pathlib import Path

from base import BrandLabTestCase, SKILL_ROOT

FIXTURES = SKILL_ROOT / "tests" / "fixtures" / "shopify"
STORE = "brandlab-test.myshopify.com"


class ShopifyModeTestCase(BrandLabTestCase):
    def init_shopify(self, workspace: Path, *, enable_mutations: bool = False) -> None:
        self.run_cli(
            "init_workspace.py", "--workspace", str(workspace), "--brand", "Miss Sixty",
            "--mode", "shopify_controlled", "--source-language", "en",
            "--working-language", "zh-CN", "--target-market", "IT:it-IT",
        )
        args = [
            "shopify_setup.py", "--workspace", str(workspace), "--store", STORE,
            "--allow-reads", "--fixture", str(FIXTURES / "setup-ok.json"),
        ]
        if enable_mutations:
            args += ["--enable-mutations", "--reviewer", "Jayce"]
        self.run_cli(*args)

    def discover_product(self, workspace: Path, fixture: Path | None = None) -> None:
        self.run_cli(
            "shopify_discover.py", "--workspace", str(workspace), "--target-locale", "it-IT",
            "--resource-type", "product", "--provider", "fixture", "--fixture",
            str(fixture or (FIXTURES / "discovery-product.json")),
        )

    def prepare_approved_job(self, workspace: Path, job_id: str = "m2-job") -> dict:
        self.discover_product(workspace)
        self.run_cli(
            "create_job.py", "--workspace", str(workspace), "--target-locale", "it-IT",
            "--resource-type", "product_title", "product_description", "seo_title", "seo_description",
            "--job-id", job_id,
        )
        job_dir = workspace / "jobs" / job_id
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", job_id)
        worklist = [json.loads(line) for line in (job_dir / "worklist.jsonl").read_text().splitlines()]
        self.assertEqual(len(worklist), 4)
        output = {
            "product_title": "Minigonna leopardo",
            "product_description": "<p>Minigonna leopardo</p>{{size}}",
            "seo_title": "Minigonna leopardo",
            "seo_description": "Una minigonna leopardo.",
        }
        rows = [{
            "item_id": row["item_id"], "translated_text": output[row["resource_type"]],
            "source_digest": row["source_digest"], "source_locale": "en", "target_locale": "it-IT",
        } for row in worklist]
        (job_dir / "model-translations.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", job_id, "--input", "model-translations.jsonl")
        self.run_cli("build_review.py", "--workspace", str(workspace), "--job", job_id)
        self.run_cli("apply_approval.py", "--workspace", str(workspace), "--job", job_id, "--decision", "approve", "--item-id", "all", "--reviewer", "Jayce")
        return json.loads((job_dir / "manifest.json").read_text())

    def prepare_approved_plan(self, workspace: Path, *, enable_mutations: bool = False, batch_id: str = "m2-batch") -> tuple[Path, dict, dict]:
        self.init_shopify(workspace, enable_mutations=enable_mutations)
        manifest = self.prepare_approved_job(workspace)
        self.run_cli(
            "build_mutation_plan.py", "--workspace", str(workspace), "--job", "m2-job", "--batch-id", batch_id,
        )
        plan_path = workspace / "shopify" / "mutation-plans" / f"{batch_id}.json"
        plan = json.loads(plan_path.read_text())
        return plan_path, plan, manifest

    def load_json(self, path: Path) -> dict:
        return json.loads(path.read_text())

    def read_script(self, name: str) -> str:
        return (SKILL_ROOT / "scripts" / name).read_text()

    def history_events(self, workspace: Path) -> list[str]:
        return [json.loads(line)["event_type"] for line in (workspace / "history/events.jsonl").read_text().splitlines()]
