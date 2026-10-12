---
name: brandlab-translation
description: Initialize a file-based brand translation workspace, import content, apply brand guidelines and glossary, translate, validate, review, approve, export, and optionally execute approved product updates through Shopify CLI.
---

# BrandLab Translation Skill

BrandLab is file-first. Treat `brandlab/` as the source of truth. Do not invent backend state.

## Operating rules

1. Default to Pure Skill Mode.
2. Never overwrite user files during initialization.
3. Use active guidelines and glossary for every translation.
4. Run deterministic validation before review.
5. Require explicit human approval before export.
6. Export approved items only.
7. Append state changes to `brandlab/history/events.jsonl`.
8. Finish every workflow with a concise summary and next action.
9. Keep Shopify authentication in Shopify CLI. Never store or display Shopify tokens.

## Setup

Run:

```bash
python3 scripts/init_workspace.py --brand "Brand" --mode pure --source-language en --working-language zh-CN --target-market IT:it-IT
```

If the user has no source files, ask them to place CSV, JSON, JSONL, Markdown, HTML, or text files in `brandlab/inbox/`.

## Standard workflow

1. Initialize or inspect the workspace:

   ```bash
   python3 scripts/status.py --workspace brandlab
   ```

2. Import inbox files:

   ```bash
   python3 scripts/import_content.py --workspace brandlab --source-locale en --target-locales it-IT
   ```

3. Audit imported content:

   ```bash
   python3 scripts/audit_content.py --workspace brandlab --target-locale it-IT
   ```

4. Create a job. Statuses default to all actionable catalog items:

   ```bash
   python3 scripts/create_job.py --workspace brandlab --target-locale it-IT --resource-type product_title product_description
   ```

   Inspect config, guidelines, and glossary at any time:

   ```bash
   python3 scripts/load_config.py --workspace brandlab
   ```

5. Prepare model translation work:

   ```bash
   python3 scripts/translate_job.py --workspace brandlab --job JOB_ID
   ```

   Read `jobs/JOB_ID/worklist.jsonl` and `jobs/JOB_ID/translation-prompt.md`. Write generated records to `jobs/JOB_ID/model-translations.jsonl` using this shape:

   ```json
   {"item_id":"...","translated_text":"...","source_digest":"sha256:...","target_locale":"it-IT"}
   ```

6. Ingest and validate model output:

   ```bash
   python3 scripts/translate_job.py --workspace brandlab --job JOB_ID --input model-translations.jsonl
   ```

7. Build review:

   ```bash
   python3 scripts/build_review.py --workspace brandlab --job JOB_ID
   ```

8. Ask the user to review `jobs/JOB_ID/review.md`. Do not approve without explicit instruction.

9. Apply an explicit approval decision:

   ```bash
   python3 scripts/apply_approval.py --workspace brandlab --job JOB_ID --decision approve --item-id all --reviewer "Jayce"
   ```

10. Export approved items:

   ```bash
   python3 scripts/build_export.py --workspace brandlab --job JOB_ID --format shopify_csv
   ```

## Validation policy

Hard failures block approval:

- empty output
- source digest mismatch
- target locale mismatch
- placeholder mismatch
- HTML tag mismatch
- locked glossary mismatch
- forbidden term or regex
- missing required regex
- length violation

Soft warnings need human review:

- preferred glossary term missing
- suspected untranslated source
- duplicate translation
- preserve-rule warning

## Shopify Controlled Execution

Shopify Controlled Execution is optional Mode 3. It reads product, collection, page, blog, and market data for translation planning, but currently writes only Product fields:

- `title`
- `descriptionHtml`
- `seo.title`
- `seo.description`

Complete setup first:

```bash
python3 scripts/shopify_setup.py --workspace brandlab --store STORE.myshopify.com --allow-reads
```

Then run read-only discovery:

```bash
python3 scripts/shopify_discover.py --workspace brandlab --target-locale it-IT --resource-type product
```

Discovery snapshots enter the normal translation, validation, review, and approval workflow. After translations are approved, create and review a product mutation plan:

```bash
python3 scripts/build_mutation_plan.py --workspace brandlab --job JOB_ID
python3 scripts/approve_mutation_plan.py --workspace brandlab --plan brandlab/shopify/mutation-plans/PLAN.json --reviewer "Reviewer"
```

Execution is a separate command:

```bash
python3 scripts/execute_mutation_plan.py --workspace brandlab --plan brandlab/shopify/mutation-plans/PLAN.json --execute
python3 scripts/verify_execution.py --workspace brandlab --result brandlab/shopify/execution-results/RESULT.json
```

Execution rules:

1. Enable `shopify.allow_mutations` only through an explicit setup run with `--enable-mutations --reviewer`.
2. Mutation requires approved translations and an approved plan.
3. `--execute` must be a separate instruction.
4. One batch has at most 10 items.
5. A failed batch stops; BrandLab does not retry automatically.
6. Destructive operations are not supported.
7. Raw output and command metadata are retained locally.
