# BrandLab Workflows

This document describes the supported operator workflows. Replace `brandlab` with your workspace name if you use another directory.

## 1. Pure Skill Mode

Use this mode when BrandLab produces reviewed files but does not connect to Shopify.

### Initialize

```bash
python3 scripts/init_workspace.py \
  --workspace brandlab \
  --brand "Brand" \
  --mode pure \
  --source-language en \
  --working-language zh-CN \
  --target-market IT:it-IT
```

Next: put source files in `brandlab/inbox/`.

### Import and audit

```bash
python3 scripts/status.py --workspace brandlab
python3 scripts/import_content.py --workspace brandlab --source-locale en --target-locales it-IT
python3 scripts/audit_content.py --workspace brandlab --target-locale it-IT
```

Supported inbox formats are CSV, JSON, JSONL, Markdown, HTML, and plain text.

### Create a translation job

```bash
python3 scripts/create_job.py \
  --workspace brandlab \
  --target-locale it-IT \
  --resource-type product_title product_description \
  --job-id italian_products
```

Omit `--resource-type` to include all actionable catalog items when that behavior is supported by the current script.

### Generate and ingest translations

Prepare the work:

```bash
python3 scripts/translate_job.py --workspace brandlab --job italian_products
```

Read `brandlab/jobs/italian_products/worklist.jsonl` and `translation-prompt.md`. Write model output to `model-translations.jsonl`, then ingest it:

```bash
python3 scripts/translate_job.py \
  --workspace brandlab \
  --job italian_products \
  --input model-translations.jsonl
```

### Review, approve, and export

```bash
python3 scripts/build_review.py --workspace brandlab --job italian_products
python3 scripts/apply_approval.py \
  --workspace brandlab \
  --job italian_products \
  --decision approve \
  --item-id all \
  --reviewer "Reviewer Name"
python3 scripts/build_export.py \
  --workspace brandlab \
  --job italian_products \
  --format shopify_csv
```

Approval and export are separate actions. Export includes approved items only.

## 2. Shopify setup and new translation

Use this mode when BrandLab reads from Shopify and writes only after explicit approvals.

```bash
python3 scripts/shopify_setup.py \
  --workspace brandlab \
  --store STORE.myshopify.com \
  --allow-reads
python3 scripts/shopify_discover.py \
  --workspace brandlab \
  --target-locale it-IT \
  --resource-type product
```

After discovery, use the same translation job flow as Pure Mode. To write approved changes to default product fields:

```bash
python3 scripts/build_mutation_plan.py --workspace brandlab --job JOB_ID
python3 scripts/approve_mutation_plan.py \
  --workspace brandlab \
  --plan brandlab/shopify/mutation-plans/PLAN.json \
  --reviewer "Reviewer Name"
python3 scripts/execute_mutation_plan.py \
  --workspace brandlab \
  --plan brandlab/shopify/mutation-plans/PLAN.json \
  --execute
python3 scripts/verify_execution.py \
  --workspace brandlab \
  --result brandlab/shopify/execution-results/RESULT.json
```

For market translations, use the Translation API workflow in section 3.

## 3. Existing market audit and safe market writeback

This workflow checks translations that already exist in Shopify.

### Discover and audit

```bash
python3 scripts/shopify_discover_existing.py --workspace brandlab --market MX
python3 scripts/audit_existing_translations.py --workspace brandlab --market MX
```

Optional AI review:

```bash
python3 scripts/audit_existing_translations.py \
  --workspace brandlab \
  --market MX \
  --ai-review brandlab/reports/existing-translation-audit-MX-ai-review.jsonl
```

### Create and complete a correction job

```bash
python3 scripts/create_correction_job.py \
  --workspace brandlab \
  --market MX \
  --ids ITEM_ID_1,ITEM_ID_2 \
  --job-id correction_job
python3 scripts/translate_job.py --workspace brandlab --job correction_job
# Write jobs/correction_job/model-translations.jsonl, then ingest it.
python3 scripts/translate_job.py \
  --workspace brandlab \
  --job correction_job \
  --input model-translations.jsonl
python3 scripts/build_review.py --workspace brandlab --job correction_job
python3 scripts/apply_approval.py \
  --workspace brandlab \
  --job correction_job \
  --decision approve \
  --item-id all \
  --reviewer "Reviewer Name"
```

### Plan, approve, execute, and verify

```bash
python3 scripts/build_translation_writeback_plan.py \
  --workspace brandlab \
  --job correction_job
python3 scripts/approve_mutation_plan.py \
  --workspace brandlab \
  --plan brandlab/shopify/translation-writeback-plans/PLAN.json \
  --reviewer "Reviewer Name"
# Preflight does not write to Shopify.
python3 scripts/execute_translation_writeback.py \
  --workspace brandlab \
  --plan brandlab/shopify/translation-writeback-plans/PLAN.json
# Execute only after reviewing the preflight.
python3 scripts/execute_translation_writeback.py \
  --workspace brandlab \
  --plan brandlab/shopify/translation-writeback-plans/PLAN.json \
  --execute
python3 scripts/verify_translation_writeback.py \
  --workspace brandlab \
  --result brandlab/shopify/translation-writeback-results/RESULT.json
```

Do not treat the batch as complete until every item is `verified`.

## 4. Market onboarding

Preview a market without changing configuration:

```bash
python3 scripts/onboard_market.py \
  --workspace brandlab \
  --market-id US \
  --locale en-US
```

Apply the market only after confirmation:

```bash
python3 scripts/onboard_market.py \
  --workspace brandlab \
  --market-id US \
  --locale en-US \
  --confirm-update
```

The report identifies whether the market is `translation`, `localization_only`, or `manual_only`.

## 5. Cross-market summary

```bash
python3 scripts/build_market_summary.py --workspace brandlab
```

Output:

- `brandlab/reports/market-translation-summary.json`
- `brandlab/reports/market-translation-summary.md`
