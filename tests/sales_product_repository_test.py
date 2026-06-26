from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sales_recommendation_agent.product_repository import ProductRepository


class SalesProductRepositoryTest(unittest.TestCase):
    def test_load_product_candidate_from_published_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {
                "document_info": {
                    "document_id": "doc_test",
                    "document_type": "申请表",
                    "title": "精品专线申请表",
                    "product_name": "精品专线",
                    "product_family": "互联网专线",
                    "carrier": "中国电信",
                    "region": "上海",
                    "document_status": "active",
                    "filename": "精品专线.docx",
                    "source_path": "data/raw/精品专线.docx",
                    "source_file_type": "docx",
                },
                "base_package": {
                    "packages": [
                        {
                            "package_name": "100M 精品专线",
                            "speed": "100M",
                            "price": 688,
                            "currency": "CNY",
                            "billing_period": "月",
                            "source_evidence": "100M 精品专线 688元/月",
                            "confidence": 0.9,
                        }
                    ],
                    "included_items": [{"name": "固定公网 IP"}],
                },
                "optional_packages": [
                    {
                        "name": "上行升速包",
                        "package_type": "paid_optional_package",
                        "fee_summary": "100元/月",
                    }
                ],
                "fee_and_term_rules": [
                    {
                        "rule_type": "monthly_fee",
                        "name": "月费",
                        "amount": 688,
                        "currency": "CNY",
                        "billing_period": "月",
                    }
                ],
                "eligibility_and_constraints": [
                    {
                        "constraint_type": "recommendation_blocker",
                        "name": "停用不可推荐",
                        "blocks_recommendation": True,
                    }
                ],
                "agreement_rules": [],
                "extraction_meta": {
                    "raw_category": {
                        "category_path": "电信政企/精品专线",
                        "category_levels": ["电信政企", "精品专线"],
                    }
                },
            }
            (root / "doc_test.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            result = ProductRepository(root).load_result()

            self.assertEqual(result.product_count, 1)
            self.assertEqual(result.error_count, 0)
            product = result.products[0]
            self.assertEqual(product.document_id, "doc_test")
            self.assertEqual(product.product_name, "精品专线")
            self.assertEqual(product.category_path, "电信政企/精品专线")
            self.assertEqual(len(product.packages), 1)
            self.assertEqual(product.packages[0].included_items[0]["name"], "固定公网 IP")
            self.assertEqual(len(product.optional_packages), 1)
            self.assertEqual(len(product.fee_rules), 1)
            self.assertEqual(len(product.constraints), 1)

    def test_load_product_candidate_from_new_product_json_shape(self) -> None:
        """新产品结构下，优先从 supplementary_info 提取产品推荐信息。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {
                "application_form_info": {
                    "document_info": {
                        "document_id": "",
                        "source_file": r"E:\GitHub\产品\组网\电信\本地国内MPLS-VPN\4 申请表.docx",
                        "product_name": "",
                        "carrier": "",
                        "document_status": None,
                    },
                    "pricing_info": {
                        "base_package_prices": [
                            {
                                "name": "申请表兜底套餐",
                                "price": 100,
                                "currency": "CNY",
                                "billing_period": "月",
                            }
                        ],
                    },
                    "agreement_rules": [],
                    "eligibility_and_constraints": [],
                },
                "supplementary_info": {
                    "product_intro": {
                        "product_name": "MPLS-VPN",
                        "full_description": "适合总部和分支之间进行安全稳定的企业组网。",
                        "application_scenarios": "总部+分支、企业专网",
                    },
                    "product_keywords": {
                        "raw_keywords": "组网 点对多 MPLS VPN 企业专网",
                    },
                    "pricing_info": {
                        "source_type": "pricing_sheet",
                        "base_package_prices": [
                            {
                                "name": "本地MPLS-VPN 10M",
                                "speed": "10M",
                                "price": 1200,
                                "currency": "CNY",
                                "billing_period": "月",
                                "raw_text": "10M 1200元/月",
                            }
                        ],
                        "addon_prices": [],
                        "one_time_fees": [
                            {
                                "name": "一次性费用",
                                "amount": 1300,
                                "currency": "CNY",
                                "unit": "元",
                                "raw_text": "一次性费用 1300元",
                            }
                        ],
                        "fee_and_term_rules": [],
                        "discount_policy": [],
                    },
                },
                "extraction_meta": {},
            }
            (root / "product_test.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            result = ProductRepository(root).load_result()

            self.assertEqual(result.product_count, 1)
            self.assertEqual(result.error_count, 0)
            product = result.products[0]
            self.assertEqual(product.document_id, "product_test")
            self.assertEqual(product.product_name, "MPLS-VPN")
            self.assertEqual(product.carrier, "电信")
            self.assertEqual(product.category_path, "组网/电信/本地国内MPLS-VPN")
            self.assertEqual(product.packages[0].package_name, "本地MPLS-VPN 10M")
            self.assertEqual(product.packages[0].price, 1200)
            self.assertEqual(len(product.fee_rules), 1)
            self.assertIn("企业专网", product.keywords)


if __name__ == "__main__":
    unittest.main()
