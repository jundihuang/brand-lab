# Shopify Controlled Execution Setup

Mode 3 is optional. Pure Skill Mode remains the default.

BrandLab does not manage OAuth and never stores or displays Shopify tokens. Authentication is owned by Shopify CLI.

## Check current machine

```bash
node --version
shopify version
codex plugin list | grep 'shopify@openai-curated'
```

## Install missing prerequisites

Install Shopify CLI only after you understand the command:

```bash
npm install -g @shopify/cli@latest
```

Install the Shopify AI Toolkit plugin only after you understand the command:

```bash
codex plugin add shopify@openai-curated
```

Authenticate with Shopify CLI:

```bash
shopify store auth
```

## Configure BrandLab

```bash
python3 scripts/shopify_setup.py \
  --workspace brandlab \
  --store your-store.myshopify.com \
  --allow-reads
```

The setup report is written to `brandlab/shopify/setup-report.md`. `shopify.enabled` becomes true only when checks pass. `shopify.allow_mutations` remains false.

A blocked setup does not enable reads. Fix the failed checks, then rerun setup.

## Read store content

After setup:

```bash
python3 scripts/shopify_discover.py \
  --workspace brandlab \
  --target-locale it-IT \
  --resource-type product
```

Discovery is read-only. It can read Product, Collection, Page, Blog, and Market translation-relevant fields. The normalized snapshot is imported into `brandlab/content/catalog.jsonl` and retains Shopify resource IDs.

## Translate discovered content

```bash
python3 scripts/create_job.py --workspace brandlab --target-locale it-IT --resource-type product_title product_description seo_title seo_description
python3 scripts/translate_job.py --workspace brandlab --job JOB_ID
# Codex writes jobs/JOB_ID/model-translations.jsonl
python3 scripts/translate_job.py --workspace brandlab --job JOB_ID --input model-translations.jsonl
python3 scripts/build_review.py --workspace brandlab --job JOB_ID
python3 scripts/apply_approval.py --workspace brandlab --job JOB_ID --decision approve --item-id all --reviewer "Your Name"
```

## Controlled writes

Mode 3 currently writes only Product fields:

- `title`
- `descriptionHtml`
- `seo.title`
- `seo.description`

First enable mutation capability explicitly, with a named reviewer:

```bash
python3 scripts/shopify_setup.py \
  --workspace brandlab \
  --allow-reads \
  --enable-mutations \
  --reviewer "Your Name"
```

Then create, approve, execute, and verify one plan:

```bash
python3 scripts/build_mutation_plan.py --workspace brandlab --job JOB_ID
python3 scripts/approve_mutation_plan.py --workspace brandlab --plan brandlab/shopify/mutation-plans/PLAN.json --reviewer "Your Name"
python3 scripts/execute_mutation_plan.py --workspace brandlab --plan brandlab/shopify/mutation-plans/PLAN.json
python3 scripts/execute_mutation_plan.py --workspace brandlab --plan brandlab/shopify/mutation-plans/PLAN.json --execute
python3 scripts/verify_execution.py --workspace brandlab --result brandlab/shopify/execution-results/RESULT.json
```

The command without `--execute` is a preflight. `--execute` is intentionally separate from plan approval. Each plan contains at most 10 approved items. Failed batches stop and are never retried automatically.

Raw CLI output is saved under `brandlab/shopify/connector-output/`, and command metadata is appended to `brandlab/shopify/command-log.jsonl`.

## Security boundary

1. Shopify CLI owns authentication; BrandLab never stores or displays tokens.
2. Discovery is read-only.
3. Mutations require approved translations, an approved plan, enabled mutation capability, and separate `--execute`.
4. Destructive operations are blocked in MVP.
5. Use a Shopify test store for the first live smoke test.
