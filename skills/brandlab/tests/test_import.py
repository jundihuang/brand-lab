import json
from pathlib import Path

from base import BrandLabTestCase, SKILL_ROOT


class ImportContentTest(BrandLabTestCase):
    def prepare(self) -> Path:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        source = SKILL_ROOT / "examples" / "product-csv" / "products.csv"
        (workspace / "inbox" / "products.csv").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        glossary = SKILL_ROOT / "examples" / "product-csv" / "glossary.csv"
        (workspace / "glossary" / "glossary.csv").write_text(glossary.read_text(encoding="utf-8"), encoding="utf-8")
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        return workspace

    def test_import_creates_stable_items_without_duplicates(self) -> None:
        workspace = self.prepare()
        before = [json.loads(line) for line in (workspace / "content" / "catalog.jsonl").read_text().splitlines()]
        self.assertEqual(len(before), 8)
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        after = [json.loads(line) for line in (workspace / "content" / "catalog.jsonl").read_text().splitlines()]
        self.assertEqual(len(after), 8)
        self.assertTrue(all(item["status"] == "unchanged" for item in after))

    def test_changed_source_marks_catalog_and_job_items_stale(self) -> None:
        workspace = self.prepare()
        self.run_cli("create_job.py", "--workspace", str(workspace), "--target-locale", "it-IT", "--job-id", "stale-job")
        source = workspace / "inbox" / "products.csv"
        source.write_text(source.read_text(encoding="utf-8").replace("Leopard-print mini skirt", "Leopard-print mini skirt updated"), encoding="utf-8")
        self.run_cli("import_content.py", "--workspace", str(workspace), "--source-locale", "en")
        manifest_path = workspace / "jobs" / "stale-job" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        stale = [item for item in manifest["items"] if item["status"] == "stale"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["stale_reason"], "source_changed")
        report = (workspace / "reports" / "import-report.md").read_text()
        self.assertIn("Changed: 1", report)
