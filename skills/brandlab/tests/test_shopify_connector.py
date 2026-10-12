import json
from pathlib import Path

from brandlab_lib import BrandLabError
from shopify_case import FIXTURES, ShopifyModeTestCase
from shopify_connector import ShopifyConnector


class ShopifyConnectorTest(ShopifyModeTestCase):
    def make_connector(self, workspace: Path, *, mutations: bool = True) -> ShopifyConnector:
        self.init_shopify(workspace, enable_mutations=mutations)
        return ShopifyConnector(workspace, provider="fixture", fixture=FIXTURES / "mutation-success.json")

    def test_mutation_refuses_connector_without_approved_plan(self) -> None:
        workspace = self.temp_workspace()
        connector = self.make_connector(workspace)
        with self.assertRaises(BrandLabError) as ctx:
            connector.run_graphql("mutation { productUpdate(input: {}) { userErrors { field message } } }", operation_kind="mutation")
        self.assertIn("no approved mutation plan", str(ctx.exception))
        self.assertFalse((workspace / "shopify/connector-output/mutation_test.json").exists())

    def test_mutation_refuses_non_approved_plan(self) -> None:
        workspace = self.temp_workspace()
        connector = self.make_connector(workspace)
        connector.approved_plan = {"status": "ready_for_review"}
        with self.assertRaises(BrandLabError) as ctx:
            connector.run_graphql("mutation { productUpdate(input: {}) { userErrors { field message } } }", operation_kind="mutation")
        self.assertIn("ready_for_review", str(ctx.exception))

    def test_destructive_operations_are_blocked(self) -> None:
        workspace = self.temp_workspace()
        connector = self.make_connector(workspace)
        with self.assertRaises(BrandLabError) as ctx:
            connector.validate_graphql("mutation { productDelete(input: {}) { deletedProductId } }", mutation=True)
        self.assertIn("destructive operations are blocked", str(ctx.exception))

    def test_execute_flags_are_detected_from_local_help(self) -> None:
        workspace = self.temp_workspace()
        connector = self.make_connector(workspace)
        connector.help_runner = lambda command: (
            0,
            "USAGE shopify store execute --store STORE --json --query QUERY --variables JSON --allow-mutations\n",
            "",
        )
        self.assertEqual(connector.available_execute_flags(), {"--store", "--json", "--query", "--variables", "--allow-mutations"})

    def test_missing_required_runtime_flag_is_not_guessed(self) -> None:
        workspace = self.temp_workspace()
        connector = self.make_connector(workspace)
        connector.help_runner = lambda command: (0, "USAGE shopify store execute --store STORE --json --query QUERY\n", "")
        with self.assertRaises(BrandLabError) as ctx:
            connector.ensure_supported_flags(mutation=True)
        self.assertIn("--variables", str(ctx.exception))

    def test_fixture_mutation_records_raw_output_and_command(self) -> None:
        workspace = self.temp_workspace()
        connector = self.make_connector(workspace)
        connector.approved_plan = {"status": "approved"}
        result = connector.run_graphql(
            "mutation BrandLab($input: ProductInput!) { operation_0: productUpdate(input: $input) { userErrors { field message } } }",
            {"input": {"id": "gid://shopify/Product/1", "title": "Minigonna leopardo"}},
            operation_kind="mutation", operation_id="mutation_test",
        )
        self.assertTrue(result["output_path"].is_file())
        records = [json.loads(line) for line in (workspace / "shopify/command-log.jsonl").read_text().splitlines()]
        self.assertEqual(records[-1]["operation_id"], "mutation_test")
        self.assertEqual(records[-1]["provider"], "fixture")
        self.assertEqual(records[-1]["output_path"], "shopify/connector-output/mutation_test.json")
