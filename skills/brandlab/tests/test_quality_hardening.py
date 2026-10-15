import json

from base import BrandLabTestCase
from scripts.audit_content import audit_text
from scripts.brandlab_lib import (
    BrandLabError, extract_placeholders, inspect_html, language_suspicion,
    lint_guideline, validate_translation,
)


GUIDELINES = [{"id": "base", "version": 1, "rules": []}]
GLOSSARY = []


class QualityHardeningTest(BrandLabTestCase):
    def check_kwargs(self, **overrides):
        kwargs = {
            "source": "Welcome to our store",
            "translated": "The and shop",
            "source_digest": "sha256:" + "0" * 64,
            "expected_digest": "sha256:" + "0" * 64,
            "source_locale": "en", "target_locale": "it-IT",
            "expected_source_locale": "en", "expected_target_locale": "it-IT",
            "guidelines": GUIDELINES, "glossary": GLOSSARY,
            "other_translations": set(), "resource_type": "product_title",
        }
        kwargs.update(overrides)
        return kwargs

    def test_placeholder_extraction_is_type_aware(self) -> None:
        expected = [
            {"type": "liquid", "token": "{{ product_title }}"},
            {"type": "printf", "token": "%s"},
        ]
        # Named groups are intentionally not used in the token contract; assert types from order.
        extracted = extract_placeholders("{{ product_title }} %s")
        self.assertEqual([item["token"] for item in extracted], [item["token"] for item in expected])
        self.assertEqual(extracted[0]["type"], "liquid")
        self.assertEqual(extracted[1]["type"], "printf")

    def test_placeholder_mutation_is_rejected(self) -> None:
        state = self.check_kwargs()
        state.update(source="Size {{size}} cost {count}", translated="Taglia {{ size }} cost {count}")
        checks = validate_translation(**state)
        failed = {check["rule_id"] for check in checks if check["status"] == "failed"}
        self.assertIn("placeholder_parity", failed)

    def test_nested_icu_message_is_preserved(self) -> None:
        icu = "{count, plural, one {item} other {items}}"
        state = self.check_kwargs()
        state.update(source=f"Stock {icu}", translated=f"Stock {icu}")
        checks = validate_translation(**state)
        placeholder = next(check for check in checks if check["rule_id"] == "placeholder_parity")
        self.assertEqual(placeholder["status"], "passed")
        self.assertEqual(extract_placeholders(f"Stock {icu}")[0]["type"], "icu")

    def test_exact_locked_brand_term_is_not_suspected_untranslated(self) -> None:
        glossary = [{
            "source_term": "Brand", "target_term": "Brand",
            "target_locale": "it-IT", "status": "locked", "note": "",
        }]
        state = self.check_kwargs(
            source="Brand", translated="Brand", glossary=glossary,
        )
        checks = validate_translation(**state)
        untranslated = next(check for check in checks if check["rule_id"] == "untranslated_source")
        self.assertEqual(untranslated["status"], "passed")

    def test_broken_and_unsafe_html_are_rejected(self) -> None:
        broken = inspect_html("<p>Unclosed")
        self.assertFalse(broken.is_balanced)
        unsafe = inspect_html("<p onclick=\"evil()\">bad</p>")
        self.assertEqual(unsafe.event_handlers, ["p@onclick"])
        checks = validate_translation(
            **self.check_kwargs(source="<p>Hello</p>", translated="<p>Ciao</p><script>x</script>")
        )
        failed = {check["rule_id"] for check in checks if check["status"] == "failed"}
        self.assertIn("html_tag_parity", failed)
        self.assertIn("html_security", failed)

    def test_html_tag_and_attribute_whitelists(self) -> None:
        rules = [{
            "id": "html", "version": 1,
            "rules": [
                {"type": "html_allowed_tags", "value": ["p"]},
                {"type": "html_allowed_attributes", "value": ["href"]},
            ],
        }]
        checks = validate_translation(
            **self.check_kwargs(
                source="<p>Hello</p>", translated="<p><span>Ciao</span> <a onclick=\"x()\">link</a></p>",
                guidelines=rules,
            )
        )
        by_rule = {check["rule_id"]: check for check in checks}
        self.assertEqual(by_rule["html_allowed_tags"]["status"], "failed")
        self.assertEqual(by_rule["html_allowed_attributes"]["status"], "failed")

    def test_language_mismatch_is_a_soft_warning(self) -> None:
        checks = validate_translation(**self.check_kwargs())
        by_rule = {check["rule_id"]: check for check in checks}
        self.assertEqual(by_rule["language_mismatch"]["status"], "warning")
        self.assertEqual(by_rule["untranslated_source"]["status"], "passed")
        self.assertEqual(self.item_status(checks), "needs_review")

    def test_guideline_conflicts_are_linted(self) -> None:
        errors = lint_guideline({
            "id": "bad", "name": "Bad", "resource_type": "product_title", "target_locale": "it-IT",
            "status": "active", "version": 1,
            "rules": [
                {"type": "forbidden_term", "value": "sale"},
                {"type": "required_term", "value": "Sale"},
                {"type": "min_length", "value": 50},
                {"type": "max_length", "value": 20},
                {"type": "forbidden_regex", "pattern": "("},
            ],
        })
        rendered = " ".join(errors)
        self.assertIn("both forbidden and required", rendered)
        self.assertIn("exceeds max_length", rendered)
        self.assertIn("invalid regex", rendered)

    def test_market_format_is_linted(self) -> None:
        errors = lint_guideline({
            "id": "market", "name": "Market", "resource_type": "*", "target_locale": "it-IT",
            "market": "italy", "status": "active", "version": 1, "rules": [],
        })
        self.assertTrue(any("market: use an uppercase ISO-style market ID" in error for error in errors))

    def test_multiline_html_stays_stable(self) -> None:
        checks = validate_translation(**self.check_kwargs(
            source="<p>\n  Welcome to our store\n</p>",
            translated="<p>\n  Benvenuti nel nostro store\n</p>",
        ))
        by_rule = {check["rule_id"]: check for check in checks}
        self.assertEqual(by_rule["placeholder_parity"]["status"], "passed")
        self.assertEqual(by_rule["html_tag_parity"]["status"], "passed")

    def test_duplicate_source_content_is_flagged_for_review(self) -> None:
        checks = audit_text("Repeated source", [], [], "it-IT", duplicate_count=2)
        duplicate = next(check for check in checks if check["rule_id"] == "repeated_template_pattern")
        self.assertEqual(duplicate["status"], "warning")

    def test_guideline_lint_cli_writes_report(self) -> None:
        workspace = self.temp_workspace()
        self.init_workspace(workspace)
        (workspace / "guidelines" / "bad.yaml").write_text(
            "id: bad\nname: Bad\nresource_type: invalid\ntarget_locale: 12\nstatus: active\nversion: 1\nrules: []\n",
            encoding="utf-8",
        )
        process = self.run_cli("lint_guidelines.py", "--workspace", str(workspace), expect_success=False)
        self.assertEqual(process.returncode, 2)
        report = json.loads((workspace / "reports" / "guideline-lint.json").read_text(encoding="utf-8"))
        self.assertFalse(report["ok"])
        self.assertGreater(report["error_count"], 0)

    def item_status(self, checks):
        from scripts.brandlab_lib import item_status_from_checks
        return item_status_from_checks(checks)
