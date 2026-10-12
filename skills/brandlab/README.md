# BrandLab Translation Skill

BrandLab is a Codex-native, file-based translation governance skill. It manages brand guidelines, glossary, content catalog, translation jobs, deterministic validation, human review, approval, and export.

## Install

Python 3.10+ and PyYAML are required.

```bash
python3 -m pip install PyYAML
```

No database, backend, Shopify App, or daemon is required.

## Create a workspace

```bash
python3 scripts/init_workspace.py \
  --brand "Miss Sixty" \
  --mode pure \
  --source-language en \
  --working-language zh-CN \
  --target-market IT:it-IT
```

The command creates `./brandlab/` and prints the next action.

## Import and translate

```bash
cp your-products.csv brandlab/inbox/
python3 scripts/import_content.py --workspace brandlab --source-locale en --target-locales it-IT
python3 scripts/audit_content.py --workspace brandlab --target-locale it-IT
python3 scripts/create_job.py --workspace brandlab --target-locale it-IT --resource-type product_title product_description
python3 scripts/translate_job.py --workspace brandlab --job JOB_ID
# Codex writes jobs/JOB_ID/model-translations.jsonl
python3 scripts/translate_job.py --workspace brandlab --job JOB_ID --input model-translations.jsonl
python3 scripts/build_review.py --workspace brandlab --job JOB_ID
python3 scripts/apply_approval.py --workspace brandlab --job JOB_ID --decision approve --item-id all --reviewer "Your Name"
python3 scripts/build_export.py --workspace brandlab --job JOB_ID --format shopify_csv
```

## Try the example

```bash
python3 -m unittest discover -s tests -v
```

The E2E test runs import, audit, job creation, model-output ingestion, validation, review, approval, export, manifest creation, and history recording.

## Shopify Controlled Execution

Pure Mode remains the default. Mode 3 adds read-only Shopify discovery and controlled, approved Product updates through Shopify CLI. See `docs/SHOPIFY-SETUP.md`.

Minimum flow:

```bash
python3 scripts/shopify_setup.py --workspace brandlab --store STORE.myshopify.com --allow-reads
python3 scripts/shopify_discover.py --workspace brandlab --target-locale it-IT --resource-type product
python3 scripts/create_job.py --workspace brandlab --target-locale it-IT --resource-type product_title product_description
python3 scripts/translate_job.py --workspace brandlab --job JOB_ID --input model-translations.jsonl
python3 scripts/build_review.py --workspace brandlab --job JOB_ID
python3 scripts/apply_approval.py --workspace brandlab --job JOB_ID --decision approve --item-id all --reviewer "Your Name"
python3 scripts/build_mutation_plan.py --workspace brandlab --job JOB_ID
python3 scripts/approve_mutation_plan.py --workspace brandlab --plan brandlab/shopify/mutation-plans/PLAN.json --reviewer "Your Name"
python3 scripts/execute_mutation_plan.py --workspace brandlab --plan brandlab/shopify/mutation-plans/PLAN.json --execute
python3 scripts/verify_execution.py --workspace brandlab --result brandlab/shopify/execution-results/RESULT.json
```

BrandLab never stores or displays Shopify tokens. Discovery is read-only. Mutations are Product-only, plan-based, approved separately, capped at 10 items, and never retried automatically after failure.

## Error message standard

BrandLab errors must identify:

1. The file.
2. The invalid field or rule.
3. The reason.
4. The exact fix.
