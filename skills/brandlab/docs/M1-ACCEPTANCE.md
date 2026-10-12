# M1 Acceptance — Pure Skill MVP

- Date: 2026-10-12
- Status: Complete
- Scope: `PLAN.md` milestone M1
- Runtime: Python 3.10+, PyYAML 6+

## Verification

Command:

```bash
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -v
```

Result:

```text
Ran 9 tests
OK
```

## Completed M1 capabilities

| Plan item | Evidence |
|---|---|
| M1.1 Skill package foundation | `SKILL.md`, `README.md`, `requirements.txt`, `scripts/`, `schemas/`, `templates/`, `rules/`, `examples/`, `tests/`. |
| M1.2 Workspace initialization | `scripts/init_workspace.py`; preserves existing files; generates `setup-report.md`; tested by `tests/test_init.py`. |
| M1.3 Config, guideline, glossary contracts | `scripts/load_config.py`, `scripts/brandlab_lib.py`, brand/guideline/glossary schemas, active guideline matching, glossary enforcement. |
| M1.4 Content import | `scripts/import_content.py`; CSV, JSON, JSONL, Markdown, HTML, TXT; stable IDs; SHA-256 digest; new/changed/unchanged; stale linked jobs. |
| M1.5 Existing content audit | `scripts/audit_content.py`; empty, missing translation, language suspicion, forbidden/required terms, placeholder, HTML, SEO length, repeated pattern, legal claims, glossary consistency. |
| M1.6 Translation jobs | `scripts/create_job.py`; filters, immutable source snapshots, guideline snapshots, glossary snapshot, manifest. |
| M1.7 Translation generation | Codex generates from `worklist.jsonl`; `scripts/translate_job.py` prepares work, ingests output, records source digest, guideline versions, and glossary digest. |
| M1.8 Validation and Trust Score | `scripts/validate_translation.py`; placeholder, HTML, glossary, forbidden/required, length, duplicate, digest, locale, empty, and untranslated checks; deterministic Trust Score. |
| M1.9 Review and approval | `scripts/build_review.py`, `scripts/apply_approval.py`; hard-rule failure cannot be approved; decisions record reviewer, time, and digest. |
| M1.10 Export and history | `scripts/build_export.py`; CSV, Shopify-compatible CSV, JSON, JSONL, Markdown, HTML-safe; approved-only export; manifest; append-only history. |
| M1.11 Pure Mode E2E | `tests/test_pure_mode_e2e.py` runs import → audit → job → translation → validation → review → approval → Shopify-compatible CSV export. |

## Verified guardrails

1. Second initialization preserves user files.
2. Re-import unchanged content does not duplicate catalog items.
3. Changed source marks linked job items stale and invalidates their prior usability.
4. Hard-rule failures produce `blocked` status and cannot be approved.
5. Default export contains approved items only.
6. Every export produces a manifest.
7. State-changing operations append to `history/events.jsonl`.

## Boundary

Pure Skill Mode does not call Shopify. Shopify-compatible CSV is a file export only. Shopify Controlled Execution remains M2 scope.
