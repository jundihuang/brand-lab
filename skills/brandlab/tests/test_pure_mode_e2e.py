import json
from pathlib import Path

from base import BrandLabTestCase, SKILL_ROOT

FIXTURES = SKILL_ROOT / "tests" / "fixtures" / "pure-mode"


class PureModeE2ETest(BrandLabTestCase):
    def test_full_pure_mode_workflow(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        (workspace / "inbox" / "products.csv").write_text(
            (SKILL_ROOT / "examples/product-csv/products.csv").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (workspace / "glossary" / "glossary.csv").write_text(
            (SKILL_ROOT / "examples/product-csv/glossary.csv").read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        self.run_cli("audit_content.py", "--workspace", str(workspace), "--target-locale", "it-IT")
        self.run_cli("create_job.py", "--workspace", str(workspace), "--target-locale", "it-IT", "--job-id", "e2e-job")
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", "e2e-job")

        job = workspace / "jobs" / "e2e-job"
        worklist = [json.loads(line) for line in (job / "worklist.jsonl").read_text().splitlines()]
        self.assertEqual(len(worklist), 8)
        translations = []
        for row in worklist:
            if row["source"].startswith("Leopard-print mini"):
                text = "Minigonna stampa leopardo"
            elif row["source"].startswith("<p>A confident"):
                text = "<p>Una minigonna decisa con stampa leopardo.</p>{{size_note}}"
            elif row["source"] == "Leopard mini skirt":
                text = "Minigonna leopardo"
            elif row["source"].startswith("A bold"):
                text = "Una minigonna decisa con stampa leopardo."
            elif row["source"].startswith("Silk-blend blouse"):
                text = "Camicia in miscela di seta"
            elif row["source"].startswith("<p>A soft"):
                text = "<p>Una camicia morbida realizzata con una miscela di seta.</p>{{care_note}}"
            elif row["source"] == "Silk blend blouse":
                text = "Camicia in miscela di seta"
            else:
                text = "Una camicia morbida in miscela di seta."
            translations.append({
                "item_id": row["item_id"], "translated_text": text,
                "source_digest": row["source_digest"], "source_locale": "en", "target_locale": "it-IT",
            })
        (job / "model-translations.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in translations), encoding="utf-8"
        )
        self.run_cli("translate_job.py", "--workspace", str(workspace), "--job", "e2e-job", "--input", "model-translations.jsonl")
        self.run_cli("build_review.py", "--workspace", str(workspace), "--job", "e2e-job")
        self.run_cli("apply_approval.py", "--workspace", str(workspace), "--job", "e2e-job", "--decision", "approve", "--item-id", "all", "--reviewer", "Jayce")

        manifest = json.loads((job / "manifest.json").read_text())
        self.assertTrue(all(item["status"] == "approved" for item in manifest["items"]))
        self.assertTrue((job / "review.md").exists())
        self.assertTrue((job / "approvals.json").exists())

        self.run_cli("build_export.py", "--workspace", str(workspace), "--job", "e2e-job", "--format", "shopify_csv", "--output", "e2e.csv")
        export_files = list((workspace / "exports").glob("e2e.csv"))
        manifests = list((workspace / "exports").glob("e2e.manifest.json"))
        self.assertEqual(len(export_files), 1)
        self.assertEqual(len(manifests), 1)
        export_manifest = json.loads(manifests[0].read_text())
        self.assertEqual(export_manifest["item_count"], 8)
        self.assertEqual(export_manifest["approved_only"], True)
        csv_content = export_files[0].read_text(encoding="utf-8")
        self.assertIn("Handle,Title,Body (HTML)", csv_content)
        self.assertIn("Minigonna stampa leopardo", csv_content)

        history = (workspace / "history" / "events.jsonl").read_text()
        for event in ["workspace_initialized", "content_imported", "job_created", "translation_ingested", "approval_applied", "export_created"]:
            self.assertIn(event, history)

        final_manifest = json.loads((job / "manifest.json").read_text())
        self.assertEqual(final_manifest["status"], "exported")

        expected = json.loads((FIXTURES / "expected-summary.json").read_text())
        catalog = [json.loads(line) for line in (workspace / "content" / "catalog.jsonl").read_text().splitlines()]
        history = (workspace / "history" / "events.jsonl").read_text()
        self.assertEqual(len(catalog), expected["catalog_item_count"])
        self.assertEqual(len(manifest["items"]), expected["job_item_count"])
        self.assertEqual(export_manifest["item_count"], expected["export_item_count"])
        self.assertEqual(export_manifest["approved_only"], expected["approved_only"])
        for event in expected["required_history_events"]:
            self.assertIn(event, history)
        for handle in (FIXTURES / "expected-shopify-handles.csv").read_text().splitlines()[1:]:
            self.assertIn(handle, csv_content)
        review = (job / "review.md").read_text()
        for section in (FIXTURES / "expected-review-sections.txt").read_text().splitlines():
            self.assertIn(section, review)
