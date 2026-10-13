import json

import yaml

from base import BrandLabTestCase
from shopify_case import FIXTURES, ShopifyModeTestCase


class MarketOnboardingTest(BrandLabTestCase):
    def test_english_us_is_localization_only_and_does_not_update_without_confirmation(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        self.run_cli(
            "onboard_market.py", "--workspace", str(workspace), "--market-id", "US",
            "--locale", "en-US", "--skip-shopify-check",
        )
        report = json.loads(sorted((workspace / "onboarding").glob("market-onboarding-us-*.json"))[0].read_text())
        self.assertEqual(report["translation_mode"], "localization_only")
        self.assertFalse(report["config_updated"])
        self.assertFalse((workspace / "onboarding" / "market-onboarding-us-*.json").exists())
        content = report["content_plan"][0]
        self.assertEqual(content["action"], "market_localization_audit")
        self.assertIn("No English-to-English", content["note"])
        brand = yaml.safe_load((workspace / "brand.yaml").read_text())
        self.assertFalse(any(market.get("id") == "US" for market in brand["markets"]))


class ShopifyMarketOnboardingTest(ShopifyModeTestCase):
    def test_shopify_market_is_checked_before_translation_onboarding(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.run_cli(
            "onboard_market.py", "--workspace", str(workspace), "--market-id", "US",
            "--locale", "en-US", "--confirm-update", "--provider", "fixture",
            "--fixture", str(FIXTURES / "markets-onboarding.json"),
        )
        report = json.loads(sorted((workspace / "onboarding").glob("market-onboarding-us-*.json"))[0].read_text())
        self.assertEqual(report["translation_mode"], "localization_only")
        self.assertTrue(report["config_updated"])
        self.assertIn("Market found", " ".join(report["checks"]))
        brand = yaml.safe_load((workspace / "brand.yaml").read_text())
        us = next(market for market in brand["markets"] if market.get("id") == "US")
        self.assertEqual(us["translation_mode"], "localization_only")
        self.assertTrue(us["review_required"])
