import json
from pathlib import Path

from shopify_case import FIXTURES, ShopifyModeTestCase


class ShopifyDiscoveryTest(ShopifyModeTestCase):
    def test_fixture_discovery_imports_translatable_fields_without_live_write(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.discover_product(workspace)
        latest = self.load_json(workspace / "shopify/discovery/latest.json")
        catalog = [json.loads(line) for line in (workspace / "content/catalog.jsonl").read_text().splitlines()]
        self.assertEqual(len(catalog), 4)
        self.assertEqual({item["external_id"] for item in catalog}, {"gid://shopify/Product/1"})
        self.assertEqual({item["resource_type"] for item in catalog}, {"product_title", "product_description", "seo_title", "seo_description"})
        for item in catalog:
            self.assertEqual(item["context"]["shopify_resource_id"], "gid://shopify/Product/1")
            self.assertIn(item["context"]["shopify_field"], {"title", "descriptionHtml", "seo.title", "seo.description"})
            self.assertTrue(item["digest"].startswith("sha256:"))
            self.assertEqual(item["source_locale"], "en")
        self.assertEqual(latest["counts"] if "counts" in latest else {}, {})
        command = [json.loads(line) for line in (workspace / "shopify/command-log.jsonl").read_text().splitlines()][-1]
        self.assertEqual(command["command"], ["fixture", "read"])
        self.assertIn("shopify_discovery_completed", self.history_events(workspace))
        self.assertNotIn("shopify_execution_completed", self.history_events(workspace))

    def test_changed_discovery_marks_existing_catalog_items_changed(self) -> None:
        workspace = self.temp_workspace()
        self.init_shopify(workspace)
        self.discover_product(workspace)
        self.discover_product(workspace, FIXTURES / "discovery-product-changed.json")
        catalog = [json.loads(line) for line in (workspace / "content/catalog.jsonl").read_text().splitlines()]
        changed = {item["resource_type"]: item["status"] for item in catalog}
        self.assertEqual(changed["product_title"], "changed")
        self.assertEqual(changed["product_description"], "unchanged")
        self.assertEqual(changed["seo_title"], "unchanged")
        self.assertEqual(changed["seo_description"], "unchanged")
