import json
from pathlib import Path

import yaml

from shopify_case import FIXTURES, STORE, ShopifyModeTestCase


class ShopifySetupTest(ShopifyModeTestCase):
    def test_missing_toolkit_blocks_setup_and_does_not_enable_shopify(self) -> None:
        workspace = self.temp_workspace()
        self.run_cli(
            "init_workspace.py", "--workspace", str(workspace), "--brand", "Miss Sixty",
            "--mode", "shopify_controlled", "--source-language", "en",
            "--working-language", "zh-CN", "--target-market", "IT:it-IT",
        )
        process = self.run_cli(
            "shopify_setup.py", "--workspace", str(workspace), "--store", STORE,
            "--allow-reads", "--fixture", str(FIXTURES / "setup-missing-toolkit.json"),
            expect_success=True,
        )
        brand = yaml.safe_load((workspace / "brand.yaml").read_text())
        self.assertEqual(process.stdout.count('"result": "blocked"'), 1)
        self.assertFalse(brand["shopify"]["enabled"])
        self.assertFalse(brand["shopify"]["setup_completed"])
        self.assertFalse(brand["shopify"]["allow_reads"])
        self.assertFalse(brand["shopify"]["allow_mutations"])
        self.assertIn("codex plugin add shopify-plugin@shopify-ai-toolkit", (workspace / "shopify/setup-report.md").read_text())
        self.assertIn("shopify_setup_completed", self.history_events(workspace))

    def test_successful_fixture_setup_enables_reads_but_not_mutations(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        brand = yaml.safe_load((workspace / "brand.yaml").read_text())
        self.assertEqual(brand["mode"], "shopify_controlled")
        self.assertTrue(brand["shopify"]["enabled"])
        self.assertTrue(brand["shopify"]["setup_completed"])
        self.assertTrue(brand["shopify"]["allow_reads"])
        self.assertFalse(brand["shopify"]["allow_mutations"])
        for directory in ["discovery", "mutation-plans", "execution-results", "connector-output"]:
            self.assertTrue((workspace / "shopify" / directory).is_dir())

    def test_mutations_require_explicit_enable_and_reviewer(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        process = self.run_cli(
            "shopify_setup.py", "--workspace", str(workspace), "--allow-reads",
            "--enable-mutations", "--fixture", str(FIXTURES / "setup-ok.json"),
            expect_success=False,
        )
        self.assertEqual(process.returncode, 2)
        brand = yaml.safe_load((workspace / "brand.yaml").read_text())
        self.assertFalse(brand["shopify"]["allow_mutations"])
        self.init_shopify(workspace, enable_mutations=True)
        brand = yaml.safe_load((workspace / "brand.yaml").read_text())
        self.assertTrue(brand["shopify"]["allow_mutations"])
        self.assertEqual(brand["shopify"]["mutation_enabled_by"], "Jayce")
        self.run_cli(
            "shopify_setup.py", "--workspace", str(workspace), "--allow-reads",
            "--fixture", str(FIXTURES / "setup-ok.json"),
        )
        brand = yaml.safe_load((workspace / "brand.yaml").read_text())
        self.assertFalse(brand["shopify"]["allow_mutations"])
