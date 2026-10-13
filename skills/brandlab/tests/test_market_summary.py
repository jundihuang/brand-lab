import json

from base import BrandLabTestCase


class MarketSummaryTest(BrandLabTestCase):
    def test_summary_reports_enabled_market_without_shopify_calls(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        self.run_cli("build_market_summary.py", "--workspace", str(workspace))
        summary = json.loads((workspace / "reports/market-translation-summary.json").read_text())
        self.assertEqual([row["market"] for row in summary["markets"]], ["IT"])
        self.assertFalse(summary["markets"][0]["discovery"]["discovered"])
        self.assertFalse(summary["markets"][0]["audit"]["audited"])
        self.assertIn("did not call Shopify", (workspace / "reports/market-translation-summary.md").read_text())
