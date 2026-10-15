# Release Notes

## v0.3.0 - 2026-10-15

BrandLab v0.3.0 adds market-aware translation checking and safe Shopify Translation API writeback.

### Highlights

1. Check existing Shopify market translations and produce a structured report.
2. Support paired-source audit when source content is available and target-only audit when it is not.
3. Add optional AI review to existing translation audits.
4. Turn audit findings into correction jobs with validation and human approval.
5. Write approved market translations with Shopify `translationsRegister`.
6. Separate translation approval, plan approval, preflight, execution, and read-back verification.
7. Support market onboarding, including localization-only markets such as US English.
8. Add cross-market summary reporting.
9. Add cursor-based Shopify discovery pagination.
10. Support bridge source strategy and audit policy behavior.

### Safety

1. Shopify reads remain controlled by `shopify.allow_reads`.
2. Shopify writes remain controlled by `shopify.allow_mutations`.
3. Mutation batches are capped at 10 items.
4. Failed batches stop instead of retrying automatically.
5. Every writeback records history, rollback source, and read-back result.
6. BrandLab never stores or displays Shopify tokens.

### Documentation

1. `docs/WORKFLOWS.md`
2. `docs/FILE-CONTRACTS.md`
3. `docs/TROUBLESHOOTING.md`
4. `docs/V0.3.0-ACCEPTANCE.md`
5. `docs/V0.3.0-RELEASE-PLAN.md`

### Known limitations

1. No social platform API publishing.
2. No automatic image generation or upload.
3. No multi-user admin backend.
4. No background queue.
5. No automatic writeback without human approval.
6. Not every Shopify translatable resource is supported.
