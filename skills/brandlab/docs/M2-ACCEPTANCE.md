# Milestone 2 Acceptance — Shopify Controlled Execution

Date: 2026-10-12  
Scope: `docs/PLAN.md`, Milestone 2  
Result: **PASS (fixture-based)**

The first live-store smoke test remains a separate operational gate. It is not part of automated M2 acceptance.

## Execution boundary

Mode 3 is Shopify Controlled Execution.

- Discovery supports Product, Collection, Page, Blog, and Market read-only fields.
- Mutation MVP supports Product only.
- Supported mutation fields are `title`, `descriptionHtml`, `seo.title`, and `seo.description`.
- Shopify authentication remains with Shopify CLI; BrandLab stores and displays no token.
- Defaults are `enabled=false`, `allow_reads=false`, and `allow_mutations=false`.
- Mutation execution requires approved translations, an approved mutation plan, explicit mutation capability, and a separate `--execute` command.
- Batch maximum is 10. A failed batch stops and is not retried.
- Destructive operations are blocked.
- Live setup verifies the selected store through `shopify store info --store`; fixture setup accepts a `store_authenticated` boolean and therefore does not prove store identity.

## Evidence summary

Automated verification command:

```bash
python3 -m compileall -q scripts tests
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

Current result:

```text
Ran 30 tests in 8.245s
OK
```

The suite includes the original 9 M1 tests and 21 new M2 tests. Tests use local fixtures and make no live Shopify request.

## M2.1 Setup detection and guidance — PASS

Planned scope:

1. Detect Node.js 18+.
2. Detect Shopify CLI.
3. Detect Shopify AI Toolkit.
4. Detect authenticated selected store.
5. Provide installation guidance.
6. Require confirmation before installation/auth.
7. Enable Shopify only after checks pass.
8. Keep mutations false by default.

Implementation evidence:

- `scripts/shopify_setup.py`
- `templates/shopify-setup-report.md`
- `docs/SHOPIFY-SETUP.md`

Test evidence:

- `tests/test_shopify_setup.py::ShopifySetupTest::test_missing_toolkit_blocks_setup_and_does_not_enable_shopify`
- `tests/test_shopify_setup.py::ShopifySetupTest::test_successful_fixture_setup_enables_reads_but_not_mutations`
- `tests/test_shopify_setup.py::ShopifySetupTest::test_mutations_require_explicit_enable_and_reviewer`

Observed behavior:

- Missing AI Toolkit produces a blocked report and installation command.
- Blocked setup leaves `enabled=false`, `setup_completed=false`, `allow_reads=false`, and `allow_mutations=false`.
- Successful fixture setup enables read-only Mode 3.
- Mutation capability remains false unless setup is rerun with `--enable-mutations --reviewer`.
- Successful setup creates `shopify/discovery/`, `shopify/mutation-plans/`, `shopify/execution-results/`, and `shopify/connector-output/`.

## M2.2 Connector adapter — PASS

Planned scope:

1. Thin Shopify connector.
2. Runtime CLI flag detection.
3. GraphQL operation validation.
4. Raw output retention.
5. Command metadata retention.
6. Fixture support.

Implementation evidence:

- `scripts/shopify_connector.py`
- `brandlab/shopify/connector-output/`
- `brandlab/shopify/command-log.jsonl`

Test evidence:

- `tests/test_shopify_connector.py::ShopifyConnectorTest::test_mutation_refuses_connector_without_approved_plan`
- `tests/test_shopify_connector.py::ShopifyConnectorTest::test_mutation_refuses_non_approved_plan`
- `tests/test_shopify_connector.py::ShopifyConnectorTest::test_destructive_operations_are_blocked`
- `tests/test_shopify_connector.py::ShopifyConnectorTest::test_execute_flags_are_detected_from_local_help`
- `tests/test_shopify_connector.py::ShopifyConnectorTest::test_missing_required_runtime_flag_is_not_guessed`
- `tests/test_shopify_connector.py::ShopifyConnectorTest::test_fixture_mutation_records_raw_output_and_command`

Observed behavior:

- Mutation requests require `allow_mutations=true` and an approved plan in both live and fixture providers.
- `productDelete`, `collectionDelete`, `pageDelete`, and `blogDelete` are rejected.
- Supported execute flags are parsed from local `shopify store execute --help`; missing flags are not guessed.
- Fixture requests still write raw output and append command records.

## M2.3 Controlled discovery and catalog import — PASS

Planned scope:

1. Confirm controlled mode, setup, and read permission.
2. Query translation-relevant fields.
3. Normalize resources into catalog items.
4. Generate stable IDs and source digests.
5. Import discovery snapshot.
6. Preserve Shopify resource IDs.
7. Produce discovery report.

Implementation evidence:

- `scripts/shopify_discover.py`
- `schemas/shopify-discovery.schema.json`
- `brandlab/shopify/discovery/`

Test evidence:

- `tests/test_shopify_discovery.py::ShopifyDiscoveryTest::test_fixture_discovery_imports_translatable_fields_without_live_write`
- `tests/test_shopify_discovery.py::ShopifyDiscoveryTest::test_changed_discovery_marks_existing_catalog_items_changed`

Observed behavior:

- Product discovery normalizes `product_title`, `product_description`, `seo_title`, and `seo_description`.
- Catalog items preserve `context.shopify_resource_id` and `context.shopify_field`.
- Source digest uses SHA-256.
- First discovery creates four new product items.
- Changed source title marks only that catalog item changed; unchanged fields remain unchanged.
- Fixture discovery records a fixture read command and does not execute a Shopify mutation.

## M2.4 Mutation planning — PASS

Planned scope:

1. Select approved items only.
2. Recheck source and approval digests.
3. Build before/after operations.
4. Generate product mutation GraphQL.
5. Generate machine plan and human preview.
6. Keep plan unapproved until explicit approval.

Implementation evidence:

- `scripts/build_mutation_plan.py`
- `scripts/mutation_lib.py`
- `schemas/mutation-plan.schema.json`
- `brandlab/shopify/mutation-plans/`

Test evidence:

- `tests/test_shopify_mutation_plan.py::ShopifyMutationPlanTest::test_approved_items_plan_includes_before_after_and_digests`
- `tests/test_shopify_mutation_plan.py::ShopifyMutationPlanTest::test_plan_rejects_items_before_translation_approval`
- `tests/test_shopify_mutation_plan.py::ShopifyMutationPlanTest::test_batch_larger_than_configured_limit_is_rejected`
- `tests/test_shopify_mutation_plan.py::ShopifyMutationPlanTest::test_cancelled_plan_cannot_be_approved`

Observed behavior:

- Only approved Product fields enter the plan.
- Unapproved items are rejected and no plan file is written.
- Each operation records `before`, `after`, `source_digest`, `approval_digest`, `rollback_source`, and `status=planned`.
- A configured batch limit below the selected item count stops planning.
- A cancelled plan cannot be approved; only `ready_for_review` can become approved.

## M2.5 Approved batch execution — PASS

Planned scope:

1. Require explicit plan approval.
2. Require separate execution instruction.
3. Enforce batch maximum.
4. Recheck approval and digests.
5. Save raw CLI output.
6. Stop on failed batch.
7. Do not retry.
8. Record history and update statuses.

Implementation evidence:

- `scripts/execute_mutation_plan.py`
- `brandlab/shopify/execution-results/`
- `brandlab/shopify/command-log.jsonl`

Test evidence:

- `tests/test_shopify_execution.py::ShopifyExecutionTest::test_plan_without_approval_cannot_execute`
- `tests/test_shopify_execution.py::ShopifyExecutionTest::test_preflight_does_not_execute_or_change_statuses`
- `tests/test_shopify_execution.py::ShopifyExecutionTest::test_successful_fixture_execution_and_verification_update_all_state`
- `tests/test_shopify_execution.py::ShopifyExecutionTest::test_user_errors_fail_batch_block_items_and_prevent_retry`
- `tests/test_shopify_execution.py::ShopifyExecutionTest::test_fixture_execution_requires_enabled_mutations_and_existing_fixture`

Observed behavior:

- `ready_for_review` plans cannot execute.
- Preflight changes neither plan nor job status and records no execution.
- Successful fixture execution sets plan to `executed`, result items to `success`, and job items to `exported`.
- Raw output and command metadata are retained.
- Shopify `userErrors` set result/job to failed and items to blocked.
- Retrying the failed plan is rejected; the plan remains failed with one execution record.
- Fixture execution also requires existing fixture, enabled mutation capability, and approved plan.

## M2.6 Execution verification — PASS

Planned scope:

1. Read execution result.
2. Read back successful Product mutations.
3. Compare returned values with approved `after`.
4. Mark `verified` or `mismatched`.
5. Update plan and job statuses.
6. Write verification summary.
7. Append history.

Implementation evidence:

- `scripts/verify_execution.py`
- `schemas/execution-result.schema.json`

Test evidence:

- `tests/test_shopify_execution.py::ShopifyExecutionTest::test_successful_fixture_execution_and_verification_update_all_state`
- `tests/test_shopify_execution.py::ShopifyExecutionTest::test_verification_detects_readback_mismatch`

Manual fixture evidence:

```bash
ROOT=/Users/jayce/beansmile/codes/skills/brandlab
TMP=/tmp/brandlab-m2-discover-debug
RESULT="$TMP/shopify/execution-results/m2-batch_20261012T164200z0800.json"
python3 "$ROOT/scripts/verify_execution.py" \
  --workspace "$TMP" \
  --result "$RESULT" \
  --provider fixture \
  --fixture "$TMP/readback-fixture.json"
```

Observed output:

```json
{"result": "verified", "batch_id": "m2-batch"}
```

Observed behavior:

- Matching read-back marks all four result, plan, and job items `verified`.
- The job returns to `approved`; the plan is `executed`.
- `.verification.md` is written.
- History contains `shopify_execution_verified`.
- A mismatched title becomes `mismatched`; the plan operation becomes `mismatched` and the job fails so later batches are blocked.

## Additional fixes included

1. `verify_execution.py` now imports and calls `save_job`.
2. Absolute result paths are canonicalized so `/tmp` and `/private/tmp` resolve consistently.
3. Blocked setup no longer retains read permission.
4. Connector mutation authorization is enforced for fixture and live providers.
5. Connector no longer contains an ineffective read-permission check in the mutation path.
6. Cancelled mutation plans cannot be approved.
7. Execution preflights mutation capability before mutating plan state.
8. Controlled workspace setup now gives the correct next action.

## M2 completion decision

M2 is complete for automated fixture acceptance. The implementation and evidence cover every M2.1–M2.6 item in `docs/PLAN.md`.

The next operational gate is a test-store live smoke test with one Product and one approved field mutation.
