# BrandLab File Contracts

This document describes the files used by current workflows. Paths are relative to a workspace such as `brandlab/`.

## Brand profile

Path: `brand.yaml`

```yaml
brand: Brand Name
mode: pure # or shopify_controlled
source_language: en
working_language: zh-CN
default_tone: premium, confident, minimal

markets:
  - id: IT
    market: IT
    locale: it-IT
    shopify_locale: it
    enabled: true
    source_strategy: base # base, bridge, existing_translation
    translation_mode: translation # translation, localization_only, manual_only
    review_required: true
    audit_policy: standard # standard, seo_and_legal, custom
    audit_mode: auto # auto, paired_source, target_only

shopify:
  enabled: true
  setup_completed: true
  store: store.myshopify.com
  allow_reads: true
  allow_mutations: false
  max_batch_size: 10
```

`shopify.allow_mutations` must be `false` except for an explicitly approved execution window.

## Guideline

Path: `guidelines/*.yaml`

```yaml
id: product-title-it
name: Italian product titles
resource_type: product_title
target_locale: it-IT
market: IT
status: active
version: 1

rules:
  - type: instruction
    rule: Keep the brand name unchanged.
  - type: max_length
    value: 70
```

Translation and audit outputs record the guideline ID and version.

## Glossary

Path: `glossary/glossary.csv`

| Field | Required | Meaning |
|---|---|---|
| `source_term` | yes | Source term |
| `target_term` | yes | Approved target term |
| `target_locale` | yes | Locale the mapping applies to |
| `status` | yes | Usually `locked` or `preferred` |
| `note` | no | Reviewer guidance |

The active glossary digest is recorded with translations and audits.

## CSV field mappings

Path: `field-mappings.yaml`

This file is optional. Use it when source columns are not standard Shopify-style headers, or when the Shopify product CSV output needs localized/custom field names.

```yaml
import_csv:
  Titolo: product_title
  Descrizione: product_description

export_shopify:
  product_title: Title
  product_description: Body (HTML)
  seo_title: SEO Title
  seo_description: SEO Description
```

Import and export scripts merge this file with built-in defaults. `Handle`, `Target Locale`, `BrandLab Item IDs`, and `Source Digests` are reserved Shopify export fields.

## Content catalog

Path: `content/catalog.jsonl`

Each line is one JSON object:

```json
{
  "id": "item_407cccee90322fbc",
  "external_id": "gid://shopify/Product/123",
  "resource_type": "product_title",
  "source": "Gift Card",
  "source_locale": "en",
  "target_locale": "it",
  "market": "IT",
  "digest": "sha256:...",
  "status": "new",
  "source_file": "shopify/discovery/latest-source-content.jsonl",
  "context": {"shopify_resource_id": "gid://shopify/Product/123"}
}
```

`digest` detects source changes. A changed source must not reuse an old approval.

## Translation job

Job directory: `jobs/JOB_ID/`

### `manifest.json`

Important fields:

```json
{
  "job_id": "JOB_ID",
  "job_type": "translation",
  "status": "review",
  "mode": "pure",
  "source_locale": "en",
  "target_locale": "it-IT",
  "guidelines": [{"id": "product-title-it", "version": 1}],
  "glossary_digest": "sha256:...",
  "items": []
}
```

For market corrections, `job_type` is `market_translation_correction`, and items may also contain `translatable_content_digest`, `writeback_status`, and `verification`.

### `worklist.jsonl`

Each line tells the model what to translate:

```json
{
  "item_id": "ITEM_ID",
  "resource_type": "product_title",
  "source": "Gift Card",
  "source_digest": "sha256:...",
  "source_locale": "en",
  "target_locale": "it"
}
```

### `model-translations.jsonl`

```json
{
  "item_id": "ITEM_ID",
  "translated_text": "Carta Regalo",
  "source_digest": "sha256:...",
  "source_locale": "en",
  "target_locale": "it"
}
```

### `translations/ITEM_ID.json`

The normalized translation snapshot. It records translated text, source digest, guideline versions, and glossary digest.

### `audits/ITEM_ID.audit.json`

Validation result:

```json
{
  "item_id": "ITEM_ID",
  "status": "translated",
  "trust_score": 100,
  "checks": [
    {"rule_id": "html_tag_parity", "status": "passed", "severity": "hard"}
  ]
}
```

### `review.md`

Human-readable review packet. This file is for review; `approvals.json` is the machine-readable approval state.

## Existing translation audit

Path: `reports/existing-translation-audit-MARKET.json` and `.md`

Top-level fields include `audit_id`, `market`, `target_locale`, `audit_mode`, `source_availability`, `counts`, and `ai_review_count`.

Item fields include:

- `item_id`
- `shopify_resource_id`
- `field_key`
- `audit_mode`
- `source_available`
- `source_text`
- `published_text`
- `status`
- `recommendation`
- `trust_score`
- `checks`
- `ai_review`

Audit statuses are `pass`, `warning`, `fail`, `missing`, and source-availability states such as `source_missing`.

## Translation writeback plan

Path: `shopify/translation-writeback-plans/BATCH_ID.json`

Important fields:

```json
{
  "batch_id": "BATCH_ID",
  "plan_type": "translation_register",
  "job_id": "JOB_ID",
  "mode": "shopify_controlled",
  "store": "store.myshopify.com",
  "status": "ready_for_review",
  "shopify_locale": "it",
  "review_digest": "sha256:...",
  "graphql": {"query": "mutation ...", "variables": {}},
  "operations": []
}
```

Each operation records the resource ID, field, target locale, before value, approved after value, source digest, translatable content digest, approval digest, and rollback source.

Statuses:

1. `ready_for_review`
2. `approved`
3. `executing`
4. `executed`
5. `failed`

## Execution result and verification

Path: `shopify/translation-writeback-results/EXECUTION_ID.json`

An executed result contains one status per item. The read-back verifier updates each item to `verified` or `mismatched`, sets the top-level result status, and writes `EXECUTION_ID.verification.md`.

## Approval state

Path: `jobs/JOB_ID/approvals.json`

Root shape: `{"decisions": []}`. Each decision records `item_id`, `decision`, `recorded_status`, `reviewer`, `source_digest`, `timestamp`, and optional `note`. This file is the machine-readable source for approval digests.

## Discovery manifests

- Shopify source discovery: `shopify/discovery/MARKET-manifest.json`
- Existing translation discovery: `shopify/existing-translations/MARKET-manifest.json`
- Market onboarding: `onboarding/market-onboarding-MARKET-DATE.json`

Manifests point to their normalized JSONL records and raw diagnostic output. Operators use manifests as indexes; generated JSONL records are validated row by row.

## Translation worklist

Path: `jobs/JOB_ID/worklist.jsonl`

Each row records the item ID, source text and digest, source and target locales, and relevant guideline context. `translate_job.py` writes this worklist before prompting the model.

## Translation writeback result

Path: `shopify/translation-writeback-results/EXECUTION_ID.json`

The execution result records execution/batch IDs, store and locale, top-level status, raw output path, and one item status. Verification updates top-level and item status to `verified` or `mismatched`.

## Content audit

Path: `reports/content-audit.jsonl`

Each row records `item_id`, `status` (`ok`, `needs_review`, or `blocked`), `trust_score`, guideline versions, checks, and recommendation.

## History

Path: `history/events.jsonl`

The file is append-only. Events include approval, execution, verification, failure, setup changes, and report generation. Do not rewrite history manually.
