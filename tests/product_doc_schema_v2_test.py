from __future__ import annotations

import unittest

from agent.product_doc_agent.document_classifier import classify_document
from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES, modules_for_document
from prompts.product_doc_agent_prompts import build_module_prompt
from schema import get_module_json_schema, get_module_output_template, make_empty_product_document


class ProductDocSchemaV2Test(unittest.TestCase):
    def test_all_new_modules_have_schema_template_and_prompt(self) -> None:
        for module_name in EXTRACTION_MODULES:
            with self.subTest(module_name=module_name):
                schema = get_module_json_schema(module_name)
                template = get_module_output_template(module_name)
                prompt = build_module_prompt(module_name, "测试 Markdown")

                self.assertIn(schema["type"], {"object", "array"})
                self.assertIsNotNone(template)
                self.assertIn("测试 Markdown", prompt)
                self.assertIn(module_name.rsplit(".", 1)[-1], prompt)

    def test_empty_product_document_uses_new_top_level_shape(self) -> None:
        data = make_empty_product_document(generated_at="2026-06-18T00:00:00")

        self.assertEqual(set(data), {"application_form_info", "supplementary_info", "extraction_meta"})
        self.assertIn("document_info", data["application_form_info"])
        self.assertIn("product_intro", data["supplementary_info"])
        self.assertEqual(data["extraction_meta"]["generated_at"], "2026-06-18T00:00:00")
        self.assertEqual(
            set(data["extraction_meta"]),
            {"generated_at", "source_file", "method", "validation_issues", "validation_issue_count"},
        )

    def test_classifier_modules_are_valid_schema_modules(self) -> None:
        filenames = [
            "1 产品介绍.docx",
            "2 资费表.xlsx",
            "3 关键字.docx",
            "4 申请表_互联网专线之精品专线营销活动申请登记表.docx",
            "4-2 申请手续提示.txt",
        ]

        valid_modules = set(EXTRACTION_MODULES)
        for filename in filenames:
            with self.subTest(filename=filename):
                classification = classify_document(filename)
                self.assertTrue(classification.target_modules)
                self.assertTrue(set(classification.target_modules) <= valid_modules)
                self.assertEqual(modules_for_document(filename), list(classification.target_modules))


if __name__ == "__main__":
    unittest.main()
