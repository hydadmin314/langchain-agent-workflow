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


if __name__ == "__main__":
    unittest.main()
