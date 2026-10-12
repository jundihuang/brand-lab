from pathlib import Path
import yaml
from base import BrandLabTestCase


class InitWorkspaceTest(BrandLabTestCase):
    def test_creates_complete_workspace_and_preserves_existing_user_files(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        for relative in [
            "brand.yaml", "guidelines", "glossary/glossary.csv", "inbox", "content/catalog.jsonl",
            "jobs", "approvals", "exports", "reports", "history/events.jsonl", "setup-report.md",
        ]:
            self.assertTrue((workspace / relative).exists(), relative)
        brand = yaml.safe_load((workspace / "brand.yaml").read_text())
        self.assertEqual(brand["mode"], "pure")
        report = (workspace / "setup-report.md").read_text()
        self.assertIn("Next action", report)

        marker = workspace / "guidelines" / "user-marker.txt"
        marker.write_text("preserve-me", encoding="utf-8")
        self.init_workspace(workspace)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve-me")

    def test_invalid_mode_is_rejected(self) -> None:
        process = self.run_cli(
            "init_workspace.py", "--workspace", str(self.temp_workspace()), "--brand", "X",
            "--mode", "invalid", "--source-language", "en", "--working-language", "zh-CN",
            "--target-market", "IT:it-IT", expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("mode must be pure or shopify_controlled", process.stderr)
