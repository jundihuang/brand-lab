# Shopify Translation API notes

## Read model

`translatableResource` returns the base translatable content and translations.

```graphql
query {
  translatableResource(resourceId: "gid://shopify/Product/1") {
    resourceId
    translatableContent {
      key
      value
      digest
      locale
    }
    translations(locale: "es") {
      key
      value
      market { id }
      outdated
      updatedAt
    }
  }
}
```

Key rules:

1. `translatableContent.digest` is required by `translationsRegister`.
2. `translations` can include unpublished locales.
3. `market: null` means the translation is not scoped to one market.
4. `outdated: true` means source content changed after translation.
5. Match resources by Shopify resource ID and field key. Do not infer a market from locale alone.
6. `Shop.primaryLocale` was removed from the API; use root-level `shopLocales` to find the primary locale.

## Write model

Use `translationsRegister`; do not use `productUpdate` for market-specific translations.

```graphql
mutation translationsRegister($resourceId: ID!, $translations: [TranslationInput!]!) {
  translationsRegister(resourceId: $resourceId, translations: $translations) {
    translations {
      key
      value
      locale
      market { id }
    }
    userErrors {
      field
      message
    }
  }
}
```

`TranslationInput` uses:

```text
key
locale
marketId
translatableContentDigest
value
```

Minimum scopes:

```text
read_translations
write_translations
read_locales
```

Market discovery uses `read_markets`. Market mutation is not part of BrandLab v0.3.
`read_locales` is required for the root-level `shopLocales` query.

## BrandLab safety rules

1. Translation approval and writeback-plan approval are separate.
2. Execution requires an approved plan and explicit `--execute`.
3. Before execution, BrandLab re-reads every content digest and blocks on mismatch.
4. Any `userErrors` marks the batch failed and stops the workflow.
5. A batch has at most 10 operations.
6. Read-back verification must pass before the writeback is complete.
7. Every plan keeps the published value as rollback source.
8. Raw Shopify output and command metadata remain in the workspace.

## Current validation status

Fixture-covered:

1. translation plan construction
2. separate plan approval
3. preflight without execution
4. digest re-read and mismatch blocking logic
5. `translationsRegister` execution
6. read-back verification

Live validated on `jayce-plus-store` (read-only, 2026-10-13):

1. `shopLocales` returns published locales (en primary, it published).
2. `translatableResources(resourceType: PRODUCT)` returns source content with correct `digest`.
3. `translations(locale: "it")` returns published Italian translations.
4. Existing translation audit detected one mismatched translation (source content differs from published translation).
5. Writeback mutation has not been live-tested yet.
