# BrandLab Troubleshooting

## Quick triage

1. Run `python3 scripts/status.py --workspace brandlab`.
2. Read the latest report in `brandlab/reports/` or `brandlab/shopify/setup-report.md`.
3. Check the last events in `brandlab/history/events.jsonl`.
4. Do not edit generated state files unless the repair steps below say to do so.

## Workspace and CLI issues

### `getcwd: cannot access parent directories`

The shell is inside a deleted directory.

```bash
cd /path/to/project
pwd
python3 scripts/status.py --workspace brandlab
```

### JSON or JSONL parse error

1. Copy the affected file to a backup outside the workspace.
2. Identify the invalid line with `python3 -m json.tool` for JSON or inspect the reported JSONL line.
3. Repair only the invalid record.
4. Run `status.py` or the failing command again.

Never delete `history/events.jsonl`.

## Import and validation issues

### CSV is not imported as expected

1. Confirm UTF-8 encoding.
2. Remove an unnecessary byte-order mark (BOM) or keep it only if your source system requires it.
3. Confirm required source and locale columns.
4. Quote multiline HTML fields according to CSV rules.
5. Re-run `import_content.py` and review the import report.

### Placeholder or HTML validation fails

Examples:

- `placeholder_parity`
- `preserve_placeholders`
- `html_tag_parity`
- `empty_translation`
- `untranslated_source`

Fix the translation so placeholders and HTML tags match the source. Do not remove placeholders to make validation pass. After changing `model-translations.jsonl`, re-ingest it with `translate_job.py --input ...`.

### Digest mismatch or source changed

A source digest mismatch means the source changed after translation or approval.

1. Re-import or rediscover the source.
2. Create a new job or correction job.
3. Translate and approve the new snapshot.
4. Build a new plan.

Do not reuse the old approved translation.

## Shopify setup and authorization

### Prerequisites fail

Check:

```bash
node --version
shopify --version
shopify store info --store STORE.myshopify.com
```

Install or update Shopify CLI if needed:

```bash
npm install -g @shopify/cli@latest
```

### Store authentication fails

Run the Shopify CLI login flow outside Codex if the browser cannot be opened automatically:

```bash
shopify store auth
shopify store info --store STORE.myshopify.com
```

If an OAuth callback port remains occupied, close the old process or restart the terminal, then retry.

### `invalid_scope` or permission denied

The installed CLI session is missing a required scope. Re-authenticate with the scopes listed in `docs/SHOPIFY-SETUP.md` and `docs/SHOPIFY-TRANSLATION-API-NOTES.md`. Then rerun setup.

BrandLab never stores or displays Shopify tokens.

### `allow_mutations is false`

This is the safe default.

1. Complete translation review and plan approval.
2. Explicitly enable mutations for the execution window and record the reviewer.
3. Run preflight, execute, and read-back verification.
4. Restore `allow_mutations: false`.

Do not enable mutations globally for convenience.

## Shopify API and writeback issues

### Plan changed after review

Error examples:

- `plan changed after review`
- `plan changed after approval`

Regenerate the plan, review it again, and approve the new plan digest. Never manually edit an approved plan.

### `translatableContentDigest` mismatch

Shopify reports that the resource changed since discovery.

1. Rerun existing-market discovery.
2. Rerun audit.
3. Create a new correction job for affected items.
4. Approve and rebuild the writeback plan.
5. Approve the new plan before execution.

### GraphQL schema or nullability mismatch

Shopify may reject variables whose type does not exactly match the API. Update the query to match the API schema, add a fixture test, and rerun the workflow.

Known current requirements:

- Use root-level `shopLocales`.
- Use `Translation.market { id }`.
- Pass `resourceType` to `translatableResources`.
- Use `[ID!]!` when `translatableResourcesByIds.resourceIds` requires a non-null list.

### Execution succeeded but verification failed

1. Do not rerun the same batch automatically.
2. Open `shopify/translation-writeback-results/RESULT.json`.
3. Compare `after`, `translated`, and `readback`.
4. Check whether the value was edited in Shopify after execution.
5. Create a new correction plan for any mismatch.

### Failed batch

Execution stops on the first failure. The plan and job are marked failed or blocked. Inspect `userErrors`, fix the affected item, and build a new batch. Never resume a partially executed batch by blind retry.

## Review and approval issues

### Approval is missing

A plan cannot execute unless:

1. The job item is approved.
2. The approval decision exists in `approvals.json`.
3. The source digest still matches.
4. The plan itself is approved.

Re-run `build_review.py`, review the packet, and apply approval.

### Wrong reviewer or accidental approval

Do not edit approval state silently. Create a rejection or correction record, regenerate the review packet, and have the correct reviewer approve the corrected item.

## Market configuration issues

### Locale and Shopify locale differ

BrandLab locale and Shopify locale are intentionally separate. For example, `es-MX` may map to Shopify `es`. Set `shopify_locale` in the market configuration or pass the supported CLI override.

### Market was not found in Shopify

Create and activate the market in Shopify, then rerun onboarding. `--skip-shopify-check` is for local planning only; it does not prove that Shopify is ready.

## When to stop

Stop and ask a human before continuing if:

1. A plan digest changes unexpectedly.
2. Shopify returns an unexpected mutation result.
3. A source or translatable-content digest no longer matches.
4. Verification is not `verified`.
5. History and generated state disagree.
