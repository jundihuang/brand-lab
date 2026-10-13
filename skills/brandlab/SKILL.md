---
name: brandlab-translation
description: Help operators translate, audit, and correct brand content such as products, pages, blogs, and SEO fields using brand guidelines and glossaries; validate, review, approve, export approved content, and optionally write approved product updates back to Shopify.
---

# BrandLab Translation Skill

BrandLab is file-first. Treat `brandlab/` as the source of truth. Do not invent backend state.

## Communication style

The default user is an e-commerce operator, not a developer.

1. Reply in the user's language.
2. Lead with the business outcome: what will be translated, checked, reviewed, exported, or written back.
3. Ask only for information needed to proceed, such as source language, target language, market, file location, or store.
4. Do not expose internal terms such as Pure Skill Mode, mutation plan, JSONL, digest, or Python script names unless the user asks for technical detail or the command is required for installation, authorization, or confirmation.
5. Use operator-friendly equivalents: “普通内容翻译”, “Shopify 回写”, “回写计划”, “审核文件”, and “操作记录”.
6. When approval is needed, summarize the target language, item count, affected fields, risks, and the exact confirmation the user should give.
7. End with a concise next action.

Example operator-facing summary:

```text
已导入 24 条英文商品内容，准备翻译成意大利语。
下一步：我会先生成审核文件；在你批准前，不会导出或写回 Shopify。
```

Show commands only when they are necessary for setup, troubleshooting, or an explicit execution gate.

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

## Existing market translation audit

Use this workflow when a market such as MX or IT already has translations. Discovery and audit are read-only.

1. Discover published Shopify translations:

   ```bash
   python3 scripts/shopify_discover_existing.py --workspace brandlab --market MX
   ```

2. Audit the snapshot. If source content is available, use `paired_source`; if not, BrandLab uses `target_only` and reports the checks it cannot verify:

   ```bash
   python3 scripts/audit_existing_translations.py --workspace brandlab --market MX
   ```

3. If the user asks for AI Review, create `reports/existing-translation-audit-MX-ai-review.jsonl` from the current audit JSON. Preserve each item ID, audit mode, source digest, and published digest. Then rerun:

   ```bash
   python3 scripts/audit_existing_translations.py --workspace brandlab --market MX --ai-review brandlab/reports/existing-translation-audit-MX-ai-review.jsonl
   ```

   Never add mistranslation, omission, or addition findings to a `target_only` item. Keep target-only confidence at `0.8` or lower. AI Review does not change Shopify.

4. After the user selects audit items, create a local correction job:

   ```bash
   python3 scripts/create_correction_job.py --workspace brandlab --market MX --ids ITEM_ID_1,ITEM_ID_2
   ```

   Use `--all-recommended` only when the user has agreed to process explicit recommendations. Use `--mode rewrite` for a `target_only` item when the user wants a rewrite.

5. Reuse the standard translation, review, and approval workflow. A correction review shows the published text, source reference availability, audit mode, correction mode, and whether semantic validation is available.

After translation approval, market corrections can use Shopify Translation API writeback:

```bash
python3 scripts/build_translation_writeback_plan.py --workspace brandlab --job JOB_ID
python3 scripts/approve_mutation_plan.py --workspace brandlab --plan brandlab/shopify/translation-writeback-plans/PLAN.json --reviewer "Reviewer"
python3 scripts/execute_translation_writeback.py --workspace brandlab --plan brandlab/shopify/translation-writeback-plans/PLAN.json
python3 scripts/execute_translation_writeback.py --workspace brandlab --plan brandlab/shopify/translation-writeback-plans/PLAN.json --execute
python3 scripts/verify_translation_writeback.py --workspace brandlab --result brandlab/shopify/translation-writeback-results/RESULT.json
```

Never bypass the separate writeback approval and `--execute` gate.

## Market onboarding

Use this workflow after the user asks to add a market. Always create and show the onboarding plan first.

```bash
python3 scripts/onboard_market.py --workspace brandlab --market-id US --locale en-US
```

The command identifies:

- whether Shopify market and locale are active
- whether this is translation or localization-only
- the complete `brand.yaml` update
- guideline and glossary recommendations
- the content scan or audit plan

Apply the registry change only after explicit confirmation:

```bash
python3 scripts/onboard_market.py --workspace brandlab --market-id US --locale en-US --confirm-update
```

An English source plus `en-US` target is localization-only. BrandLab does not create an English-to-English translation job.

## Cross-market report

After discovery, audit, correction, or writeback work, generate one local summary:

```bash
python3 scripts/build_market_summary.py --workspace brandlab
```

It reports each enabled market's discovery coverage, audit counts, correction progress, writeback status, and target-only audit limits.
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
