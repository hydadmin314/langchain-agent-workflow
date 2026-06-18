from __future__ import annotations

import unittest

from agent.product_doc_agent.schema_normalizer import ProductDocumentNormalizer
from agent.product_doc_agent.validator import ProductDocumentValidator
from schema import make_empty_product_document


class ProductDocNormalizerValidatorTest(unittest.TestCase):
    def test_pricing_sheet_has_priority_over_application_form_duplicates(self) -> None:
        document = make_empty_product_document(generated_at="2026-06-18T00:00:00")
        document["extraction_meta"].update(
            {
                "source_file": "E:/product_data/premium_line",
                "method": "test",
                "extra_debug": "should be removed",
            }
        )
        document["application_form_info"]["pricing_info"]["base_package_prices"] = [
            {
                "name": "Premium Line",
                "speed": "100M",
                "upstream_speed": "100M",
                "downstream_speed": "100M",
                "bandwidth_unit": "M",
                "has_voice": False,
                "price": 3388,
                "currency": "元",
                "unit": "月",
                "billing_period": "月",
                "contract_period": "",
                "included_items": ["5 IP"],
                "conditions": [],
                "description": "",
                "raw_text": "100M 3388元/月",
            }
        ]
        document["supplementary_info"]["pricing_info"]["base_package_prices"] = [
            {
                "name": "Premium Line 100M monthly",
                "speed": "100M",
                "upstream_speed": "100M",
                "downstream_speed": "100M",
                "bandwidth_unit": "M",
                "has_voice": False,
                "price": 3388,
                "currency": "元",
                "unit": "",
                "billing_period": "月付",
                "contract_period": "",
                "included_items": ["5 IP"],
                "conditions": [],
                "description": "",
                "raw_text": "100M | 3388 | NaN",
            }
        ]

        result = ProductDocumentNormalizer().normalize_product_document(document)
        normalized = result.product_document

        self.assertEqual(normalized["application_form_info"]["pricing_info"]["base_package_prices"], [])
        sheet_item = normalized["supplementary_info"]["pricing_info"]["base_package_prices"][0]
        self.assertEqual(sheet_item["currency"], "CNY")
        self.assertEqual(sheet_item["billing_period"], "月")
        self.assertEqual(sheet_item["unit"], "元/月")
        self.assertEqual(
            set(normalized["extraction_meta"]),
            {"generated_at", "source_file", "method", "validation_issues", "validation_issue_count"},
        )
        self.assertTrue(any("保留资费表" in issue["message"] for issue in result.issues))

    def test_validator_reports_cross_source_duplicates_before_normalization(self) -> None:
        document = make_empty_product_document(generated_at="2026-06-18T00:00:00")
        document["extraction_meta"].update({"source_file": "E:/product_data/premium_line", "method": "test"})
        form_item = {
            "name": "Premium Line",
            "speed": "100M",
            "upstream_speed": "100M",
            "downstream_speed": "100M",
            "bandwidth_unit": "M",
            "has_voice": False,
            "price": 3388,
            "currency": "CNY",
            "unit": "元/月",
            "billing_period": "月",
            "contract_period": "",
            "included_items": [],
            "conditions": [],
            "description": "",
            "raw_text": "100M 3388元/月",
        }
        sheet_item = dict(form_item, name="Premium Line 100M monthly")
        document["application_form_info"]["pricing_info"]["base_package_prices"] = [form_item]
        document["supplementary_info"]["pricing_info"]["base_package_prices"] = [sheet_item]

        issues = ProductDocumentValidator().validate(document)

        self.assertTrue(any("重复" in issue["message"] for issue in issues))

    def test_expands_annual_prices_from_pricing_sheet_table_rows(self) -> None:
        document = make_empty_product_document(generated_at="2026-06-18T00:00:00")
        document["supplementary_info"]["pricing_info"]["base_package_prices"] = [
            {
                "name": "Premium Line 100M",
                "speed": "100M/100M",
                "upstream_speed": "100M",
                "downstream_speed": "100M",
                "bandwidth_unit": "M",
                "has_voice": False,
                "price": 3388,
                "currency": "元",
                "unit": "",
                "billing_period": "月",
                "contract_period": "",
                "included_items": [],
                "conditions": [],
                "description": "",
                "raw_text": "100M/100M | 3388 | 28880 | 48000 | 赠送5个可用IP地址",
            }
        ]

        normalized = ProductDocumentNormalizer().normalize_product_document(document).product_document
        prices = normalized["supplementary_info"]["pricing_info"]["base_package_prices"]

        self.assertEqual([(item["price"], item["billing_period"]) for item in prices], [(3388.0, "月"), (28880.0, "年"), (48000.0, "2年")])

    def test_does_not_expand_discount_columns_as_annual_prices(self) -> None:
        document = make_empty_product_document(generated_at="2026-06-18T00:00:00")
        document["supplementary_info"]["pricing_info"]["base_package_prices"] = [
            {
                "name": "MSTP 1M",
                "speed": "1M",
                "upstream_speed": "1M",
                "downstream_speed": "1M",
                "bandwidth_unit": "M",
                "has_voice": False,
                "price": 1333,
                "currency": "元",
                "unit": "",
                "billing_period": "月",
                "contract_period": "",
                "included_items": [],
                "conditions": [],
                "description": "",
                "raw_text": "1M | 1333 | 666.5 |",
            }
        ]

        normalized = ProductDocumentNormalizer().normalize_product_document(document).product_document
        prices = normalized["supplementary_info"]["pricing_info"]["base_package_prices"]

        self.assertEqual(len(prices), 1)
        self.assertEqual(prices[0]["billing_period"], "月")


if __name__ == "__main__":
    unittest.main()
