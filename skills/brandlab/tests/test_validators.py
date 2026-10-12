from base import BrandLabTestCase
from scripts.brandlab_lib import calculate_trust_score, item_status_from_checks, make_check, validate_translation


GUIDELINES = [{"id": "title", "version": 1, "rules": [{"type": "max_length", "value": 40}]}]
GLOSSARY = [{"source_term": "mini skirt", "target_term": "minigonna", "target_locale": "it-IT", "status": "locked", "note": ""}]


class ValidatorsTest(BrandLabTestCase):
    def valid_kwargs(self, **overrides):
        kwargs = {
            "source": "Leopard mini skirt {{size}}",
            "translated": "Minigonna leopardo {{size}}",
            "source_digest": "sha256:" + "0" * 64,
            "expected_digest": "sha256:" + "0" * 64,
            "source_locale": "en", "target_locale": "it-IT",
            "expected_source_locale": "en", "expected_target_locale": "it-IT",
            "guidelines": GUIDELINES, "glossary": GLOSSARY, "other_translations": set(),
        }
        kwargs.update(overrides)
        return kwargs

    def test_valid_translation_passes_locked_term_placeholder_and_length(self) -> None:
        checks = validate_translation(**self.valid_kwargs())
        self.assertEqual(item_status_from_checks(checks), "translated")
        self.assertEqual(calculate_trust_score(checks), 100)

    def test_missing_locked_term_and_placeholder_are_hard_failures(self) -> None:
        checks = validate_translation(**self.valid_kwargs(translated="Gonna leopardo {wrong}", source_locale="fr"))
        self.assertEqual(item_status_from_checks(checks), "blocked")
        failed = {check["rule_id"] for check in checks if check["status"] == "failed"}
        self.assertIn("locked_glossary_term", failed)
        self.assertIn("placeholder_parity", failed)
        self.assertIn("source_locale_consistency", failed)

    def test_warning_does_not_block_and_reduces_score(self) -> None:
        checks = validate_translation(**self.valid_kwargs(translated="Minigonna leopardo {{size}} extra repeated content"))
        self.assertEqual(item_status_from_checks(checks), "blocked")  # max_length is hard in this fixture

        soft_guidelines = [{"id": "soft", "version": 1, "rules": [{"type": "preserve", "value": "html_tags"}]}]
        checks = validate_translation(**self.valid_kwargs(guidelines=soft_guidelines))
        self.assertEqual(item_status_from_checks(checks), "translated")
        self.assertGreaterEqual(calculate_trust_score(checks), 90)

    def test_score_is_deterministic(self) -> None:
        checks = [make_check("a", "warning", "soft"), make_check("b", "warning", "soft")]
        self.assertEqual(calculate_trust_score(checks), calculate_trust_score(list(reversed(checks))))
