from __future__ import annotations

import unittest

from agent.product_doc_agent.merger import ProductDocumentMerger


class ProductDocMergerV2Test(unittest.TestCase):
    def test_merges_dot_path_modules_into_new_schema(self) -> None:
        product_document = ProductDocumentMerger().merge(
            {
                "application_form_info.document_info": {
                    "document_id": "doc_test",
                    "source_file": "application_form.docx",
                    "product_name": "Premium Line",
                    "carrier": "Carrier Shanghai",
                    "version": "2025/B",
                    "effective_date": None,
                    "document_status": "active",
                },
                "supplementary_info.product_intro": {
                    "product_name": "Premium Line",
                    "full_description": "Intro text",
                    "application_scenarios": "Office network",
                },
                "supplementary_info.application_materials": [
                    {
                        "material_type": "license",
                        "name": "Business license copy",
                        "description": "Needs company seal",
                        "required": True,
                        "applicable_to": ["Shanghai company"],
                        "conditions": [],
                        "signature_required": False,
                        "seal_required": True,
                        "copy_required": True,
                        "original_required": False,
                        "pages_or_locations": [],
                        "handling_notes": [],
                        "related_constraints": [],
                        "raw_text": "Business license copy, sealed.",
                        "source_file": "",
                    }
                ],
            },
            document_metadata={
                "source_folder": "E:/product_data/premium_line",
                "module_source_files": {
                    "supplementary_info.application_materials": "application_materials.txt",
                },
            },
        )

        self.assertEqual(product_document["application_form_info"]["document_info"]["product_name"], "Premium Line")
        self.assertEqual(product_document["supplementary_info"]["product_intro"]["full_description"], "Intro text")
        self.assertEqual(product_document["supplementary_info"]["application_materials"][0]["name"], "Business license copy")
        self.assertEqual(
            product_document["supplementary_info"]["application_materials"][0]["source_file"],
            "application_materials.txt",
        )
        self.assertEqual(product_document["extraction_meta"]["source_file"], "E:/product_data/premium_line")
        self.assertEqual(product_document["extraction_meta"]["validation_issue_count"], 0)
        self.assertEqual(
            set(product_document["extraction_meta"]),
            {"generated_at", "source_file", "method", "validation_issues", "validation_issue_count"},
        )

    def test_merge_many_combines_outputs_from_multiple_files(self) -> None:
        product_document = ProductDocumentMerger().merge_many(
            [
                {
                    "supplementary_info.product_keywords": {"raw_keywords": "low latency static IP"},
                    "__module_source_files__": {"supplementary_info.product_keywords": "keywords.docx"},
                },
                {
                    "supplementary_info.pricing_info": {
                        "source_type": "pricing_sheet",
                        "source_file": "",
                        "one_time_fees": [],
                        "base_package_prices": [],
                        "addon_prices": [],
                        "fee_and_term_rules": [],
                        "discount_policy": [],
                    },
                    "__module_source_files__": {"supplementary_info.pricing_info": "pricing.xlsx"},
                },
            ],
            document_metadata={"source_folder": "E:/product_data/premium_line"},
        )

        self.assertEqual(product_document["supplementary_info"]["product_keywords"]["raw_keywords"], "low latency static IP")
        self.assertEqual(product_document["supplementary_info"]["pricing_info"]["source_file"], "pricing.xlsx")
        self.assertEqual(product_document["extraction_meta"]["source_file"], "E:/product_data/premium_line")


if __name__ == "__main__":
    unittest.main()
