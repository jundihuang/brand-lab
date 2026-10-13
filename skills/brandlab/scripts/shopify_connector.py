#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from brandlab_lib import (
    BrandLabError, append_history, atomic_write_json, atomic_write_text,
    load_brand, read_json, resolve_workspace, sha256_text, utc_now,
)


Runner = Callable[[list[str]], tuple[int, str, str]]


def default_runner(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(command, text=True, capture_output=True)
    return completed.returncode, completed.stdout, completed.stderr


def default_help_runner(command: list[str]) -> tuple[int, str, str]:
    return default_runner(command)


class ShopifyConnector:
    """Thin Shopify CLI adapter. Authentication remains with Shopify CLI."""

    REQUIRED_EXECUTE_FLAGS = {"--store", "--query", "--json", "--variables"}
    REQUIRED_MUTATION_FLAGS = REQUIRED_EXECUTE_FLAGS | {"--allow-mutations"}

    def __init__(
        self,
        workspace: Path,
        provider: str = "live",
        fixture: Path | None = None,
        runner: Runner | None = None,
        help_runner: Runner | None = None,
        approved_plan: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.brand = load_brand(self.workspace)
        self.config = self.load_config()
        self.provider = provider
        self.fixture = fixture.expanduser().resolve() if fixture else None
        self.runner = runner or default_runner
        self.help_runner = help_runner or default_help_runner
        self.approved_plan = approved_plan
        self._available_flags: set[str] | None = None

    def load_config(self) -> dict[str, Any]:
        config = self.brand.get("shopify")
        if not isinstance(config, dict):
            raise BrandLabError(f"{self.workspace / 'brand.yaml'}: shopify configuration is missing. Run shopify_setup.py.")
        required = {"enabled", "store", "allow_reads", "allow_mutations", "max_batch_size"}
        missing = sorted(required - set(config))
        if missing:
            raise BrandLabError(f"{self.workspace / 'brand.yaml'}: shopify is missing {missing}. Run shopify_setup.py.")
        if config["enabled"] is not True:
            raise BrandLabError(f"{self.workspace / 'brand.yaml'}: shopify.enabled is false. Complete shopify_setup.py first.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", str(config["store"])):
            raise BrandLabError(f"{self.workspace / 'brand.yaml'}: shopify.store must be a myshopify.com domain.")
        if config["max_batch_size"] > 10:
            raise BrandLabError(f"{self.workspace / 'brand.yaml'}: shopify.max_batch_size cannot exceed 10.")
        if not config.get("setup_completed"):
            raise BrandLabError(f"{self.workspace / 'brand.yaml'}: shopify.setup_completed is false. Run shopify_setup.py checks first.")
        return config

    def validate_graphql(self, query: str, mutation: bool = False) -> dict[str, Any]:
        text = query.strip()
        if not text:
            raise BrandLabError("GraphQL: query is empty. Provide a query or mutation.")
        if text.count("{") != text.count("}"):
            raise BrandLabError("GraphQL: braces are unbalanced. Fix the operation before execution.")
        operation = re.match(r"^(query|mutation)\b", text, flags=re.I)
        if not operation:
            raise BrandLabError("GraphQL: operation must start with query or mutation.")
        actual_mutation = operation.group(1).casefold() == "mutation"
        if mutation != actual_mutation:
            raise BrandLabError(
                f"GraphQL: declared kind {'mutation' if mutation else 'query'} does not match operation {operation.group(1)}."
            )
        root_names: set[str] = set()
        if actual_mutation:
            unknown_roots = set(re.findall(r"\b(productDelete|collectionDelete|pageDelete|blogDelete)\b", text, flags=re.I))
            if unknown_roots:
                raise BrandLabError(f"GraphQL mutation: destructive operations are blocked in MVP: {sorted(unknown_roots)}.")
            root_names = set(re.findall(
                r"\b(productCreate|productUpdate|collectionUpdate|pageUpdate|blogUpdate|translationsRegister)\b",
                text, flags=re.I,
            ))
            if not root_names:
                raise BrandLabError("GraphQL mutation: no supported mutation root found. Use a supported Shopify Admin mutation.")
            if "userErrors" not in text and "usererrors" not in text.casefold():
                raise BrandLabError("GraphQL mutation: response must select userErrors so partial failures cannot be hidden.")
        elif re.search(r"\bmutation\b", text, flags=re.I):
            raise BrandLabError("GraphQL query: mutation syntax is not allowed in a read-only operation.")
        return {"ok": True, "kind": "mutation" if actual_mutation else "query", "roots": sorted(root_names)}

    def available_execute_flags(self) -> set[str]:
        if self._available_flags is not None:
            return self._available_flags
        completed = self.help_runner(["shopify", "store", "execute", "--help"])
        if completed[0] != 0:
            raise BrandLabError(
                "Shopify CLI: cannot read 'shopify store execute --help'. "
                f"Exit {completed[0]}: {completed[1] or completed[2]}".strip()
            )
        self._available_flags = set(re.findall(r"(?<![A-Za-z0-9-])(--[a-z][a-z0-9-]+)", completed[1]))
        return self._available_flags

    def ensure_supported_flags(self, mutation: bool) -> None:
        available = self.available_execute_flags()
        required = self.REQUIRED_MUTATION_FLAGS if mutation else self.REQUIRED_EXECUTE_FLAGS
        missing = sorted(required - available)
        if missing:
            raise BrandLabError(
                f"Shopify CLI: required flags {missing} are not supported by the installed version. "
                "Upgrade Shopify CLI or use a version that supports the documented command."
            )

    def build_command(self, query: str, variables: dict[str, Any] | None, mutation: bool) -> list[str]:
        self.ensure_supported_flags(mutation)
        command = [
            "shopify", "store", "execute",
            "--store", str(self.config["store"]),
            "--json",
            "--query", query,
        ]
        if variables:
            if "--variables" not in self.available_execute_flags():
                raise BrandLabError("Shopify CLI: --variables is unsupported by the installed version.")
            command += ["--variables", json.dumps(variables, ensure_ascii=False, sort_keys=True)]
        if mutation:
            if self.config.get("allow_mutations") is not True:
                raise BrandLabError(
                    f"{self.workspace / 'brand.yaml'}: shopify.allow_mutations is false. Explicitly enable mutations after approval."
                )
            if not self.approved_plan:
                raise BrandLabError("Shopify mutation: no approved mutation plan was supplied to the connector.")
            if self.approved_plan.get("status") != "approved":
                raise BrandLabError(
                    f"Shopify mutation: plan status is {self.approved_plan.get('status')}; approved plan required."
                )
            command += ["--allow-mutations"]
        return command

    def fixture_response(self, operation_kind: str) -> dict[str, Any]:
        if not self.fixture or not self.fixture.is_file():
            raise BrandLabError(f"fixture: Shopify fixture not found: {self.fixture}. Pass --fixture with a JSON response file.")
        value = read_json(self.fixture)
        if isinstance(value, dict) and operation_kind in {"read", "mutation"} and operation_kind in value:
            value = value[operation_kind]
        if not isinstance(value, dict):
            raise BrandLabError(f"{self.fixture}: fixture response must be a JSON object.")
        return value

    def ensure_mutation_authorized(self) -> None:
        if self.config.get("allow_mutations") is not True:
            raise BrandLabError(
                f"{self.workspace / 'brand.yaml'}: shopify.allow_mutations is false. Explicitly enable mutations after approval."
            )
        if not self.approved_plan:
            raise BrandLabError("Shopify mutation: no approved mutation plan was supplied to the connector.")
        if self.approved_plan.get("status") != "approved":
            raise BrandLabError(
                f"Shopify mutation: plan status is {self.approved_plan.get('status')}; approved plan required."
            )

    def record_command(
        self, operation_id: str, operation_kind: str, command: list[str],
        output_path: Path, returncode: int, parsed_ok: bool, plan_batch_id: str | None = None,
    ) -> Path:
        record = {
            "timestamp": utc_now(),
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "provider": self.provider,
            "store": self.config["store"],
            "command": command,
            "returncode": returncode,
            "parsed_ok": parsed_ok,
            "output_path": str(output_path.relative_to(self.workspace)),
        }
        if plan_batch_id:
            record["batch_id"] = plan_batch_id
        log_path = self.workspace / "shopify" / "command-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return log_path

    def run_graphql(
        self, query: str, variables: dict[str, Any] | None = None,
        operation_kind: str = "read", operation_id: str | None = None,
    ) -> dict[str, Any]:
        if operation_kind not in {"read", "mutation"}:
            raise BrandLabError(f"operation_kind {operation_kind}: use read or mutation.")
        mutation = operation_kind == "mutation"
        validation = self.validate_graphql(query, mutation)
        if mutation:
            self.ensure_mutation_authorized()
        operation_id = operation_id or f"{operation_kind}_{utc_now().replace(':', '').replace('-', '', 2).replace('+', 'z')}"
        output_dir = self.workspace / "shopify" / "connector-output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{operation_id}.json"

        if self.provider == "fixture":
            response = self.fixture_response(operation_kind)
            atomic_write_json(output_path, response)
            self.record_command(operation_id, operation_kind, ["fixture", operation_kind], output_path, 0, True)
        elif self.provider == "live":
            command = self.build_command(query, variables, mutation)
            returncode, stdout, stderr = self.runner(command)
            parsed_ok = False
            response: dict[str, Any] | None = None
            if returncode == 0:
                try:
                    candidate = json.loads(stdout)
                    if isinstance(candidate, dict):
                        response = candidate
                        parsed_ok = True
                except json.JSONDecodeError:
                    parsed_ok = False
            if response is None:
                response = {
                    "brandlab_error": "Shopify CLI did not return a JSON object.",
                    "returncode": returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            atomic_write_json(output_path, response)
            self.record_command(operation_id, operation_kind, command, output_path, returncode, parsed_ok)
            if returncode != 0 or not parsed_ok:
                raise BrandLabError(
                    f"Shopify CLI operation {operation_id}: no valid JSON object returned. Inspect {output_path}."
                )
            errors = response.get("errors")
            if errors:
                raise BrandLabError(f"Shopify GraphQL operation {operation_id} returned errors. Inspect {output_path}.")
        else:
            raise BrandLabError(f"provider {self.provider}: use live or fixture.")
        return {"response": response, "output_path": output_path, "validation": validation, "operation_id": operation_id}

    def parse_data(self, result: dict[str, Any]) -> dict[str, Any]:
        response = result["response"]
        data = response.get("data")
        if not isinstance(data, dict):
            raise BrandLabError(f"Shopify response {result['operation_id']}: data is missing or not an object. Inspect {result['output_path']}.")
        return data


def load_approved_plan(path: Path) -> dict[str, Any]:
    plan = read_json(path)
    if not isinstance(plan, dict):
        raise BrandLabError(f"{path}: mutation plan must be a JSON object.")
    if plan.get("mode") != "shopify_controlled":
        raise BrandLabError(f"{path}: plan mode must be shopify_controlled.")
    if plan.get("status") != "approved":
        raise BrandLabError(f"{path}: plan status must be approved before connector mutation.")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate GraphQL or inspect supported Shopify CLI flags.")
    parser.add_argument("--workspace", default="brandlab")
    parser.add_argument("--flags", action="store_true", help="Print supported store execute flags detected from local help.")
    parser.add_argument("--query-file")
    parser.add_argument("--mutation", action="store_true")
    parser.add_argument("--provider", default="live", choices=["live", "fixture"])
    parser.add_argument("--fixture")
    args = parser.parse_args()
    try:
        workspace = resolve_workspace(Path(args.workspace))
        connector = ShopifyConnector(workspace, provider=args.provider, fixture=Path(args.fixture) if args.fixture else None)
        if args.flags:
            print(json.dumps({"supported_flags": sorted(connector.available_execute_flags())}, indent=2))
            return 0
        if not args.query_file:
            parser.error("--query-file is required unless --flags is used")
        query = Path(args.query_file).read_text(encoding="utf-8")
        print(json.dumps(connector.validate_graphql(query, args.mutation), indent=2))
        return 0
    except BrandLabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
