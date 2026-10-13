from pathlib import Path
import yaml

from base import BrandLabTestCase
from scripts.brandlab_lib import BrandLabError, load_brand


class MarketRegistryTest(BrandLabTestCase):
    def write(self, value: dict) -> Path:
        workspace = self.temp_workspace()
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / 'brand.yaml').write_text(yaml.safe_dump(value), encoding='utf-8')
        return workspace

    def valid_base(self) -> dict:
        return {
            'brand': 'Demo', 'mode': 'pure', 'source_language': 'en',
            'working_language': 'zh-CN', 'markets': [],
        }

    def test_v2_market_defaults_and_english_localization_mode(self) -> None:
        value = self.valid_base()
        value['source'] = {'default_locale': 'en'}
        value['markets'] = [
            {'market': 'MX', 'locale': 'es-MX', 'enabled': True},
            {'market': 'US', 'locale': 'en-US', 'enabled': True},
        ]
        brand = load_brand(self.write(value))
        mx, us = brand['markets']
        self.assertEqual(mx['id'], 'MX')
        self.assertEqual(mx['shopify_locale'], 'es')
        self.assertEqual(mx['translation_mode'], 'translation')
        self.assertEqual(us['translation_mode'], 'localization_only')
        self.assertEqual(us['audit_mode'], 'auto')
        self.assertTrue(us['review_required'])

    def test_explicit_fields_are_preserved(self) -> None:
        value = self.valid_base()
        value['markets'] = [{
            'id': 'NorthMX', 'market': 'MX', 'locale': 'es-MX', 'shopify_locale': 'es',
            'enabled': False, 'source_strategy': 'bridge', 'translation_mode': 'manual_only',
            'review_required': False, 'audit_policy': 'seo_and_legal', 'audit_mode': 'target_only',
        }]
        market = load_brand(self.write(value))['markets'][0]
        for key in ['id', 'shopify_locale', 'source_strategy', 'translation_mode', 'review_required', 'audit_policy', 'audit_mode']:
            self.assertEqual(market[key], value['markets'][0][key])

    def test_duplicate_market_ids_are_rejected(self) -> None:
        value = self.valid_base()
        value['markets'] = [
            {'id': 'MX', 'market': 'MX', 'locale': 'es-MX', 'enabled': True},
            {'market': 'MX', 'locale': 'es-MX', 'enabled': True},
        ]
        with self.assertRaises(BrandLabError):
            load_brand(self.write(value))

    def test_invalid_enum_is_rejected(self) -> None:
        value = self.valid_base()
        value['markets'] = [{'market': 'MX', 'locale': 'es-MX', 'enabled': True, 'audit_mode': 'wrong'}]
        with self.assertRaises(BrandLabError):
            load_brand(self.write(value))
