from __future__ import annotations

import unittest

from agent.product_doc_agent.document_classifier import (
    APPLICATION_FORM,
    APPLICATION_MATERIALS,
    PRICING_SHEET,
    PRODUCT_INTRO,
    PRODUCT_KEYWORDS,
    UNKNOWN,
    classify_document,
    normalize_document_name,
)


class ProductDocDocumentClassifierTest(unittest.TestCase):
    def test_classifies_schema_document_roles(self) -> None:
        cases = {
            "1 产品介绍.docx": PRODUCT_INTRO,
            "2 资费表.xlsx": PRICING_SHEET,
            "3 关键字.docx": PRODUCT_KEYWORDS,
            "3 关键词.docx": PRODUCT_KEYWORDS,
            "4 申请表_互联网专线之精品专线营销活动申请登记表.docx": APPLICATION_FORM,
            "4-2 申请手续提示.txt": APPLICATION_MATERIALS,
        }

        for filename, expected_role in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(classify_document(filename).role, expected_role)

    def test_keeps_out_of_scope_documents_unknown(self) -> None:
        cases = [
            "4-1 外地公司担保书.docx",
            "5 业务变更_套餐及加装包.docx",
            "6 业务变更_移机.docx",
            "7 业务拆机.docx",
            "4-3 客户基本信息表.xlsx",
            "1 【报商机】代理商商机信息登记表.docx",
            "3【20240415起】MSTP_横表.xls",
        ]

        for filename in cases:
            with self.subTest(filename=filename):
                self.assertEqual(classify_document(filename).role, UNKNOWN)

    def test_returns_target_modules(self) -> None:
        classification = classify_document("4 申请表_转定用的大客户需求表.doc")

        self.assertEqual(classification.role, APPLICATION_FORM)
        self.assertEqual(
            classification.target_modules,
            (
                "application_form_info.document_info",
                "application_form_info.parties_and_application",
                "application_form_info.pricing_info",
                "application_form_info.agreement_rules",
                "application_form_info.eligibility_and_constraints",
            ),
        )

    def test_normalizes_leading_sequence_only(self) -> None:
        self.assertEqual(normalize_document_name("4-5 本地国内MSTP申请手续提示.txt"), "本地国内MSTP申请手续提示")
        self.assertEqual(normalize_document_name("1 产品介绍.docx"), "产品介绍")


if __name__ == "__main__":
    unittest.main()
