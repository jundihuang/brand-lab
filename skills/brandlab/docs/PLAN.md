# BrandLab Translation Skill Implementation Plan

- Version: v0.1
- Date: 2026-10-12
- PRD: `docs/PRD.md` v0.2
- Goal: ship a Codex-native BrandLab Translation Skill with Pure Skill Mode first, then optional Shopify Controlled Execution Mode.

## 1. Delivery Strategy

Build in four milestones:

1. **M1 — Pure Skill MVP**: file-based workspace, translation governance, validation, review, approval, export.
2. **M2 — Shopify Controlled Execution**: setup guidance, read-only discovery, mutation planning, approved batch execution.
3. **M3 — Workflow Hardening**: schema coverage, error handling, edge cases, examples, smoke tests.
4. **M4 — Release Readiness**: installation experience, documentation, packaged examples, final acceptance.

Pure Skill Mode is the critical path. Shopify Controlled Execution starts only after Pure Mode review and approval flow is stable.

## 2. Guiding Rules

1. Default to Pure Skill Mode.
2. No backend, database, queue, multi-user UI, role system, social publishing, image generation, or expert workbench.
3. Every feature must expose a next action.
4. Human approval is mandatory before export or Shopify mutation.
5. Shopify writes are plan-based, approved, batched, recorded, and never automatic.
6. Use Python scripts for deterministic operations.
7. Every milestone ends with executable examples or tests.

## 3. Milestone 1 — Pure Skill MVP

### M1.1 Skill package foundation

Tasks:

1. Create repository structure.
2. Write `SKILL.md`.
3. Write user-facing `README.md`.
4. Define Python script entry points.
5. Add local development instructions.
6. Add basic error-message standards.

Deliverables:

```text
SKILL.md
README.md
scripts/
schemas/
templates/
rules/
examples/
tests/
```

Definition of done:

1. Codex can discover how to use the Skill from `SKILL.md`.
2. A user can understand installation and first commands from `README.md`.
3. All planned script names have one clear responsibility.

### M1.2 Workspace initialization and setup interview

Tasks:

1. Implement setup interview.
2. Support mode selection: `pure` or `shopify_controlled`.
3. Create workspace folders.
4. Create `brand.yaml`.
5. Create default guideline templates.
6. Create glossary template.
7. Create append-only `history/events.jsonl`.
8. Generate `setup-report.md`.
9. Prevent overwrite of existing user files.

Deliverables:

```text
scripts/init_workspace.py
templates/brand.yaml
templates/guideline.yaml
templates/review.md
templates/setup-report.md
schemas/brand.schema.json
```

Definition of done:

1. First initialization creates a complete workspace.
2. Second initialization does not overwrite content.
3. Invalid brand config produces actionable errors.
4. Setup report includes selected mode and next action.

### M1.3 Brand profile, guidelines, and glossary contracts

Tasks:

1. Define brand profile schema.
2. Define guideline schema.
3. Define glossary schema.
4. Implement loader and validator.
5. Match guidelines by resource type, market, locale, and status.
6. Merge compatible guidelines.
7. Report rule conflicts before translation.
8. Snapshot guidelines and glossary into each job.

Deliverables:

```text
schemas/brand.schema.json
schemas/guideline.schema.json
schemas/glossary.schema.json
scripts/load_config.py
```

Definition of done:

1. Active guidelines are applied.
2. Inactive guidelines are ignored.
3. Locked and forbidden glossary terms are enforced.
4. Guideline and glossary versions are traceable.

### M1.4 Content import and catalog

Tasks:

1. Scan `brandlab/inbox/`.
2. Support CSV, JSON, JSONL, Markdown, plain text, and HTML.
3. Normalize supported Shopify-style fields.
4. Generate stable item IDs.
5. Calculate SHA-256 source digest.
6. Detect `new`, `changed`, and `unchanged`.
7. Mark linked translations stale when source changes.
8. Generate import report.

Deliverables:

```text
scripts/import_content.py
schemas/content-item.schema.json
content/catalog.jsonl
reports/import-report.md
```

Definition of done:

1. Re-import does not duplicate unchanged content.
2. Changed source marks old translations stale.
3. User can see imported count, skipped count, new count, changed count, and next action.
4. Source locale is explicit or confirmed by the user.

### M1.5 Existing content audit

Tasks:

1. Implement audit selection by locale, resource type, and content IDs.
2. Match active guidelines.
3. Run deterministic checks.
4. Generate machine-readable audit output.
5. Generate human-readable report.
6. Recommend translate, correct, or ignore.

Checks:

1. Empty content.
2. Missing translation.
3. Suspected untranslated source.
4. Language mismatch.
5. Forbidden terms.
6. Required terms.
7. Placeholder issues.
8. HTML structure issues.
9. SEO length issues.
10. Repeated template patterns.
11. Legal or compliance issues.
12. Glossary inconsistency.

Deliverables:

```text
scripts/audit_content.py
schemas/audit-result.schema.json
reports/content-audit.md
```

Definition of done:

1. Audit does not modify source content.
2. Every item has machine-readable status.
3. User can create a translation job directly from audit recommendations.

### M1.6 Translation job creation

Tasks:

1. Implement job creation filters.
2. Snapshot selected source items.
3. Snapshot guidelines and glossary.
4. Generate unique job ID.
5. Create job manifest.
6. Set initial job status.
7. Print next action.

Filters:

1. Target locale.
2. Market.
3. Resource type.
4. Status.
5. Content IDs.
6. Source file.
7. Guideline ID.

Deliverables:

```text
scripts/create_job.py
schemas/job.schema.json
```

Definition of done:

1. Job manifest explains exact task scope.
2. Job has immutable source snapshot.
3. Two jobs with the same source do not corrupt each other.

### M1.7 Translation generation

Tasks:

1. Load job manifest.
2. Validate job is ready.
3. Load brand profile.
4. Load active guidelines.
5. Load glossary snapshot.
6. Detect or read source locale.
7. Build translation prompt context.
8. Generate translation.
9. Save each translation with metadata.
10. Link source digest, guideline versions, and glossary digest.
11. Update job status.

Supported resource types:

1. Product title.
2. Product description.
3. Product variant.
4. Collection title.
5. Collection description.
6. Page title.
7. Page body.
8. Blog title.
9. Blog body.
10. SEO title.
11. SEO description.
12. Care and compliance text.

Deliverables:

```text
scripts/translate_job.py
templates/translation-output.md
schemas/translation-output.schema.json
```

Definition of done:

1. Translation never modifies source snapshot.
2. Every output records source digest and rule versions.
3. Non-primary source content can declare source and optional bridge locale.

### M1.8 Deterministic validation and Trust Score

Tasks:

1. Implement validator runner.
2. Implement hard-rule failure handling.
3. Implement soft-rule warning handling.
4. Implement Trust Score calculation.
5. Write audit JSON per item.
6. Update item status.
7. Generate validation summary.

Validators:

1. Placeholder parity.
2. HTML tag parity.
3. Locked glossary enforcement.
4. Forbidden term detection.
5. Forbidden regex detection.
6. Required regex detection.
7. Min and max length.
8. Empty translation detection.
9. Untranslated source detection.
10. Duplicate output detection.
11. Source digest consistency.
12. Target locale consistency.

Deliverables:

```text
scripts/validate_translation.py
schemas/audit-result.schema.json
tests/test_validators.py
```

Definition of done:

1. Hard-rule failure cannot become approved.
2. Same input produces the same score.
3. Audit result explains every failed or warned check.

### M1.9 Review and approval

Tasks:

1. Generate job-level `review.md`.
2. Include source, translation, checks, Trust Score, and suggested decision.
3. Implement explicit approve, reject, and edit-request handling.
4. Store reviewer, timestamp, decision, and source digest.
5. Invalidate approval when source digest changes.
6. Prevent approval of hard-rule failures.
7. Summarize approved, rejected, blocked, and remaining counts.

Deliverables:

```text
scripts/build_review.py
scripts/apply_approval.py
templates/review.md
approvals/
```

Definition of done:

1. Review is usable without external UI.
2. Bulk approval shows exact affected scope.
3. Rejected items are excluded from default export.

### M1.10 Export and history

Tasks:

1. Filter approved items.
2. Support CSV, JSON, JSONL, Markdown, and HTML-safe output.
3. Add Shopify-compatible CSV output shape.
4. Generate export manifest.
5. Avoid overwriting previous exports.
6. Mark exported items.
7. Append export event to history.
8. Print output path and next action.

Deliverables:

```text
scripts/build_export.py
schemas/export-manifest.schema.json
exports/
history/events.jsonl
```

Definition of done:

1. Default export only includes approved items.
2. Every export has a manifest.
3. Previous exports are preserved.

### M1.11 Pure Mode end-to-end fixture

Tasks:

1. Add example product CSV.
2. Add example blog markdown.
3. Add Italian guideline examples.
4. Add glossary examples.
5. Add expected catalog, audit, translation, review, approval, and export fixtures.
6. Build a Pure Mode end-to-end test.

Deliverables:

```text
examples/product-csv/
examples/blog-markdown/
tests/test_pure_mode_e2e.py
```

Definition of done:

A user can run the example from import to export without editing code.

## 4. Milestone 2 — Shopify Controlled Execution

### M2.1 Shopify setup detection and guidance

Tasks:

1. Detect Node.js 18+.
2. Detect Shopify CLI.
3. Detect Shopify AI Toolkit.
4. Detect authenticated Shopify store.
5. Generate copy-paste installation commands.
6. Require user confirmation before running installation or auth.
7. Verify selected store.
8. Generate Shopify setup report.
9. Set `shopify.enabled` only after checks pass.
10. Keep `allow_mutations: false` by default.

Deliverables:

```text
scripts/shopify_setup.py
templates/shopify-setup-report.md
docs/SHOPIFY-SETUP.md
```

Definition of done:

1. Missing dependencies produce clear install guidance.
2. BrandLab never stores or displays Shopify tokens.
3. User cannot enter Mode 3 execution without completed setup.

### M2.2 AI Toolkit and CLI integration layer

Tasks:

1. Create a thin Shopify connector adapter.
2. Detect available CLI commands and flags at runtime.
3. Validate intended GraphQL using Shopify AI Toolkit or current schema knowledge.
4. Execute read-only queries through Shopify CLI.
5. Save raw command output.
6. Parse only expected successful response shapes.
7. Record command, timestamp, store, and output reference.

Deliverables:

```text
scripts/shopify_connector.py
shopify/discovery/
shopify/execution-results/
```

Definition of done:

1. Unsupported command flags are not guessed.
2. Every Shopify command execution is locally recorded.
3. Connector can be tested with fixtures instead of live writes.

### M2.3 Controlled discovery and catalog import

Tasks:

1. Confirm read permission and store selection.
2. Query only translation-relevant fields.
3. Normalize Shopify resources into content items.
4. Generate source digests.
5. Import normalized snapshot into content catalog.
6. Preserve Shopify resource IDs.
7. Generate discovery report.

Readable resources:

1. Product title and description.
2. Supported SEO fields.
3. Collection title and description.
4. Page title and body.
5. Blog title and body.
6. Market and locale availability.

Deliverables:

```text
scripts/shopify_discover.py
schemas/shopify-discovery.schema.json
```

Definition of done:

1. Discovery does not write to Shopify.
2. Imported items retain Shopify resource IDs.
3. Discovery snapshot can enter the normal translation workflow.

### M2.4 Mutation planning

Tasks:

1. Select approved items only.
2. Re-check source digest and approval digest.
3. Map local fields to Shopify fields.
4. Generate before and after values.
5. Generate GraphQL mutation draft.
6. Validate mutation shape.
7. Generate machine-readable mutation plan.
8. Generate human-readable mutation preview.
9. Show affected resources, fields, before/after, risk, and rollback source.
10. Keep plan in `ready_for_review` until explicit approval.

Deliverables:

```text
scripts/build_mutation_plan.py
schemas/mutation-plan.schema.json
templates/mutation-review.md
shopify/mutation-plans/
```

Definition of done:

1. No unapproved item enters a mutation plan.
2. Every operation has before/after and digests.
3. Plan review does not execute anything.

### M2.5 Approved batch execution

Tasks:

1. Require explicit plan approval.
2. Require separate execution instruction.
3. Enforce maximum batch size of 10.
4. Re-check approval and digests immediately before execution.
5. Execute one batch at a time.
6. Save raw CLI output.
7. Parse success and failure count.
8. Stop on failed batch.
9. Do not automatically retry.
10. Append execution event to history.
11. Update item status.

Deliverables:

```text
scripts/execute_mutation_plan.py
shopify/execution-results/
```

Definition of done:

1. No mutation runs without explicit approval.
2. No batch exceeds the configured maximum.
3. Failed execution stops subsequent batches.

### M2.6 Execution verification

Tasks:

1. Read parsed execution result.
2. Confirm Shopify response indicates success.
3. Run read-back verification when schema supports it.
4. Compare returned value with approved after value.
5. Mark item verified or mismatched.
6. Append verification event.
7. Generate execution summary.

Deliverables:

```text
scripts/verify_execution.py
schemas/execution-result.schema.json
```

Definition of done:

1. Every executed item has success, failed, verified, or mismatched status.
2. Mismatch blocks later batches.

## 5. Milestone 3 — Workflow Hardening

### M3.1 Schema and validation hardening

Tasks:

1. Complete JSON schemas for all persisted objects.
2. Add schema validation to every script.
3. Standardize error codes.
4. Standardize file path references.
5. Prevent partial writes using staging files and atomic replacement where practical.
6. Add corrupt-file recovery guidance.

Definition of done:

1. Invalid files fail early.
2. Error messages identify file, field, and fix.
3. Scripts do not leave ambiguous half-updated state.

### M3.2 Translation quality hardening

Tasks:

1. Improve language suspicion checks.
2. Improve HTML handling.
3. Add attribute whitelist handling.
4. Improve placeholder detection.
5. Add duplicate content detection.
6. Add translation diff report.
7. Add guideline linting.

Definition of done:

1. Common HTML and placeholder failures are caught deterministically.
2. Guideline conflicts are reported before translation.
3. Reviewers can compare source, previous translation, and new translation.

### M3.3 Import/export hardening

Tasks:

1. Improve Shopify-style CSV mapping.
2. Add field mapping configuration.
3. Handle multiline HTML safely.
4. Handle BOM and encoding issues.
5. Validate required export fields.
6. Add round-trip tests.

Definition of done:

1. CSV with multiline HTML can be imported and exported safely.
2. Export format is predictable.
3. Round-trip tests cover key field types.

### M3.4 Usability hardening

Tasks:

1. Standardize command phrases in `SKILL.md`.
2. Add a command cheat sheet.
3. Ensure every command returns next action.
4. Add troubleshooting guide.
5. Add setup and workflow FAQs.

Definition of done:

1. A new user can start from README alone.
2. Every workflow has a visible next step.
3. Common errors have recovery instructions.

## 6. Milestone 4 — Release Readiness

### M4.1 Documentation package

Deliverables:

1. `README.md`.
2. `SKILL.md`.
3. `docs/PRD.md`.
4. `docs/PLAN.md`.
5. `docs/SHOPIFY-SETUP.md`.
6. `docs/WORKFLOWS.md`.
7. `docs/FILE-CONTRACTS.md`.
8. `docs/TROUBLESHOOTING.md`.

Definition of done:

1. Pure Mode and Mode 3 are documented separately.
2. Installation, first run, translation, approval, export, and Shopify execution are all covered.
3. Examples match the current file contracts.

### M4.2 Examples and fixtures

Deliverables:

1. Product CSV example.
2. Blog markdown example.
3. Italian guideline pack.
4. Glossary pack.
5. Pure Mode end-to-end fixture.
6. Shopify discovery fixture.
7. Shopify mutation fixture.

Definition of done:

1. All examples pass local tests.
2. No example requires a real Shopify write.

### M4.3 Final acceptance tests

Pure Mode:

1. Initialize workspace.
2. Import product CSV.
3. Audit content.
4. Create Italian job.
5. Translate job.
6. Validate translations.
7. Generate review.
8. Approve eligible items.
9. Export Shopify-compatible CSV.
10. Verify manifest and history.

Mode 3 dry run:

1. Run setup detection.
2. Load discovery fixture.
3. Create translation job.
4. Translate and approve.
5. Build mutation plan.
6. Approve plan.
7. Execute against fixture connector.
8. Verify result and history.

Mode 3 live gate:

1. Run against a test store only.
2. Execute one approved batch of no more than 10 items.
3. Confirm read-back values.
4. Confirm history and execution result.
5. Confirm failed-batch stopping behavior with a controlled failure fixture.

## 7. Suggested Execution Order

### Immediate next PR

1. Create repository structure.
2. Write `SKILL.md`.
3. Write `README.md`.
4. Add core schemas.
5. Implement workspace initialization.
6. Add setup report.
7. Add Pure Mode example fixture.

### Second PR

1. Implement content import.
2. Implement content catalog.
3. Implement source digest and change detection.
4. Add import report.
5. Add import tests.

### Third PR

1. Implement guideline and glossary loading.
2. Implement existing-content audit.
3. Add audit report fixtures.
4. Add validator foundation.

### Fourth PR

1. Implement job creation.
2. Implement translation generation.
3. Implement deterministic validation.
4. Implement Trust Score.

### Fifth PR

1. Implement review generation.
2. Implement approval state.
3. Implement export and manifest.
4. Implement append-only history.
5. Add Pure Mode end-to-end test.

### Sixth PR

1. Add Shopify setup detection.
2. Add Shopify setup documentation.
3. Add discovery adapter skeleton.
4. Add fixture-based discovery tests.

### Seventh PR

1. Add mutation planning.
2. Add mutation review template.
3. Add approved execution flow.
4. Add execution verification.
5. Add Mode 3 fixture-based end-to-end test.

## 8. Test Plan

### Unit tests

1. File and path safety.
2. Source digest generation.
3. Stable ID generation.
4. Catalog change detection.
5. Guideline matching and merging.
6. Glossary enforcement.
7. Placeholder parity.
8. HTML tag parity.
9. Trust Score calculation.
10. Approval guardrails.
11. Export filtering.
12. Manifest generation.
13. History append behavior.

### Workflow tests

1. Pure Mode import-to-export.
2. Non-primary-language import.
3. Changed-source stale approval.
4. Hard-rule blocked approval.
5. Partial approval export.
6. Repeat initialization protection.

### Shopify tests

1. Missing dependency guidance.
2. Setup check state machine.
3. Discovery fixture normalization.
4. Mutation plan generation.
5. Unapproved plan cannot execute.
6. Oversized batch cannot execute.
7. Failed batch stops execution.
8. Execution result verification.
9. Execution mismatch handling.

## 9. Risks And Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Shopify CLI flags differ across versions | Execution fails or behaves unexpectedly | Detect version, read local help, validate before use, do not hardcode uncertain flags. |
| AI Toolkit schema knowledge is incomplete | Invalid GraphQL | Validate mutation drafts before planning and execution. |
| Users accidentally approve oversized writes | Store disruption | Enforce max batch size and explicit execution instruction. |
| Source changes after approval | Wrong content is written | Re-check source and approval digest before execution. |
| HTML breaks during translation | Store rendering issues | Deterministic HTML parity checks and safe field handling. |
| Non-primary source language is misdetected | Wrong translation direction | Require explicit source locale confirmation. |
| Files are manually edited inconsistently | Broken workflow state | Schema validation and actionable repair messages. |
| History becomes contradictory | Poor auditability | Append-only events and immutable snapshots. |

## 10. Immediate Next Actions

1. Create the Skill skeleton.
2. Write `SKILL.md` as the operational contract.
3. Write `README.md` as the user onboarding guide.
4. Add brand, guideline, glossary, content item, job, audit, export, and mutation plan schemas.
5. Implement `init_workspace.py` with setup interview and setup report.
6. Add a Pure Mode product CSV example.
