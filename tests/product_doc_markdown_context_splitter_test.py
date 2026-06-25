from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import unittest

from agent.product_doc_agent.document_classifier import (
    APPLICATION_FORM,
    PRICING_SHEET,
    PRODUCT_INTRO,
    UNKNOWN,
    classify_document,
)
from agent.product_doc_agent.document_loader import DocumentLoader
from agent.product_doc_agent.markdown_context_splitter import (
    build_module_contexts_from_markdown,
    render_product_module_contexts_debug,
)
from agent.product_doc_agent.markdown_renderer import MarkdownRenderer
from scripts.run_product_doc_batch import RawDocumentTask, convert_doc_to_docx_with_word


class ProductDocMarkdownContextSplitterTest(unittest.TestCase):
    def test_full_document_role_uses_full_markdown(self) -> None:
        markdown = "# 产品介绍\n\n精品专线产品介绍正文，适用于总部办公和视频会议。"

        contexts = build_module_contexts_from_markdown(
            markdown,
            max_chars=10000,
            modules=["supplementary_info.product_intro"],
            document_role=PRODUCT_INTRO,
        )

        self.assertEqual(set(contexts), {"supplementary_info.product_intro"})
        self.assertIn("精品专线产品介绍正文", contexts["supplementary_info.product_intro"])

    def test_pricing_sheet_keeps_table_context(self) -> None:
        markdown = """
| 速率 | 月付价格 | 年付价格 |
| --- | --- | --- |
| 100M | 688元/月 | 6880元/年 |
"""

        contexts = build_module_contexts_from_markdown(
            markdown,
            max_chars=10000,
            modules=["supplementary_info.pricing_info"],
            document_role=PRICING_SHEET,
        )

        self.assertIn("100M", contexts["supplementary_info.pricing_info"])
        self.assertIn("688元/月", contexts["supplementary_info.pricing_info"])

    def test_application_form_splits_into_target_modules(self) -> None:
        markdown = """
# 精品专线新装申请表

中国电信上海公司 2026版

| 字段 | 内容 |
| --- | --- |
| 客户名称 | 上海测试公司 |
| 经办人 | 张三 |
| 联系电话 | 13800000000 |
| 企业规模 | 10-30人 |
| 套餐 | 100M 月付 688元，协议期12个月 |
| 一次性接入费 | 500元/次 |

填表说明
客户欠费时服务商可暂停服务，退订时按协议承担违约责任。
外地公司办理时需提供担保书，不适用无实名资质客户。
本活动仅限企业自用，ISP、IDC、CDN经营客户不适用。
"""

        contexts = build_module_contexts_from_markdown(
            markdown,
            max_chars=10000,
            modules=[
                "application_form_info.document_info",
                "application_form_info.parties_and_application",
                "application_form_info.pricing_info",
                "application_form_info.agreement_rules",
                "application_form_info.eligibility_and_constraints",
            ],
            document_role=APPLICATION_FORM,
        )

        self.assertEqual(len(contexts), 5)
        self.assertIn("精品专线新装申请表", contexts["application_form_info.document_info"])
        self.assertIn("客户名称", contexts["application_form_info.parties_and_application"])
        self.assertNotIn("688元", contexts["application_form_info.parties_and_application"])
        self.assertIn("688元", contexts["application_form_info.pricing_info"])
        self.assertIn("违约责任", contexts["application_form_info.agreement_rules"])
        self.assertIn("外地公司", contexts["application_form_info.eligibility_and_constraints"])
        self.assertNotIn("ISP、IDC、CDN", contexts["application_form_info.agreement_rules"])
        self.assertIn("ISP、IDC、CDN", contexts["application_form_info.eligibility_and_constraints"])

    def test_application_form_uses_rule_fallback_after_pricing_without_notes_heading(self) -> None:
        markdown = """
前评估号： 折扣审批单号： 合同号：

| *速率带宽 Bandwidth | | *路由协议 Router Protocol | □静态 Static □BGP □其它 |
| --- | --- | --- | --- |
| | 一次性费用 ¥ 元 | 一次性费用 ¥ 元 | |

(八) 侮辱或者诽谤他人，侵害他人合法权益的；

四、提供网站接入服务的增值电信业务经营者应当遵守下列规定：

(一) 应当向取得相应经营许可证的基础电信业务经营者提供的网络接入等电信资源从事业务经营活动，不得向其他从事网站接入服务的增值电信业务经营者转租所获得的网络接入等电信资源。

如需使用中国联通IP地址进行互联网增值业务经营活动的，必须提供工业和信息化部颁布的相关经营资质、否则不得经营该项业务。

七、本协议费用包括月使用费和一次性费用，其中，月使用费包括网络使用费、接入电路通信费和IP地址费；

遵照《中华人民共和国电信条例》的规定，甲方逾期未支付通信费用，乙方有权要求其补缴费用，并按照所欠费用每日加收3‰的违约金。

甲方逾期未支付通信费用超过30日，乙方有权暂停服务。

甲方逾期未支付通信费用超过90日的，乙方有权终止服务，并有权依法追缴欠费和违约金。

如果甲方签约享受了乙方提供的折扣和/或优惠，并已承诺合同期限，甲方在合同期限内原则上可以申请停机，但不得申请终止服务、停机保号、变更付费方式、降低速率等可能导致原合同签订金额下降的服务。

十、甲方用于经营IDC、ISP、CDN等业务时需提供增值电信业务经营许可证，甲方不得擅自改变使用用途。
"""

        contexts = build_module_contexts_from_markdown(
            markdown,
            max_chars=10000,
            modules=[
                "application_form_info.pricing_info",
                "application_form_info.agreement_rules",
                "application_form_info.eligibility_and_constraints",
            ],
            document_role=APPLICATION_FORM,
        )

        pricing_context = contexts["application_form_info.pricing_info"]
        agreement_context = contexts["application_form_info.agreement_rules"]
        constraint_context = contexts["application_form_info.eligibility_and_constraints"]

        self.assertIn("速率带宽", pricing_context)
        self.assertIn("一次性费用", pricing_context)
        self.assertNotIn("逾期未支付通信费用", pricing_context)
        self.assertNotIn("经营许可证", pricing_context)
        self.assertIn("逾期未支付通信费用", agreement_context)
        self.assertIn("违约金", agreement_context)
        self.assertIn("合同期限", agreement_context)
        self.assertIn("经营许可证", constraint_context)
        self.assertIn("IDC、ISP、CDN", constraint_context)

    def test_application_form_moves_ocr_table_agreement_rows_out_of_pricing(self) -> None:
        markdown = """
|     | 电路租用范围 | 本地区内 |     | 速率    | **M |
|     |        |  |     | 一次性费用 | o   |
|     | 付费类型 |     |     | 缴费期 | 年付  |
| 一、 乙方同意向甲方提供服务， |     |                    |       | 并根据业务资费标准向甲方收取费用。              |     |     |       |        |                    |     | 结算币种为人民币。 |     |     |
| 停止向甲方提供服务并追回所有拖欠费用，               |                                 |     |     |             |                              | 由此造成的一切后果均由甲方承担，同时乙方保留对甲 |     |                     |     |     |             |            |     |
| 方使用业务计费期不足一个月，                    |                                 |     |     | 当月月度使用费用按照： |                              |                          |     | 月使用费*实际使用天数/当期计费周期天 |     |     |             |            |     |
| 六、 本协议费用包括月使用费和一次性费用，                     |                            |                             |     |         |               | 其中，月使用费包括本端本地线路月使用费、 |                  |                       |                  |                |             |          | 长途线 |
| 例》的规定，甲方逾期未支付通信费用，乙方有权要求其补缴费用，            |                            |                             |     |         |               |                      |                  |                       |                  | 并按照所欠费用每日加收3%的 |             |          |     |
| 违约金。                                      | 甲方逾期未支付通信费用超过30日，乙方有权暂停服务。 |                             |     |         |               |                      |                  |                       |                  | 甲方逾期未支付通信费用超过  |             |          |     |
"""

        contexts = build_module_contexts_from_markdown(
            markdown,
            max_chars=10000,
            modules=[
                "application_form_info.pricing_info",
                "application_form_info.agreement_rules",
                "application_form_info.eligibility_and_constraints",
            ],
            document_role=APPLICATION_FORM,
        )

        pricing_context = contexts["application_form_info.pricing_info"]
        agreement_context = contexts["application_form_info.agreement_rules"]
        constraint_context = contexts["application_form_info.eligibility_and_constraints"]

        self.assertIn("电路租用范围", pricing_context)
        self.assertIn("一次性费用", pricing_context)
        self.assertIn("缴费期", pricing_context)
        self.assertNotIn("乙方同意向甲方提供服务", pricing_context)
        self.assertNotIn("逾期未支付通信费用", pricing_context)
        self.assertNotIn("违约金", pricing_context)
        self.assertIn("乙方同意向甲方提供服务", agreement_context)
        self.assertIn("逾期未支付通信费用", agreement_context)
        self.assertIn("违约金", agreement_context)
        self.assertIn("当前文档没有适合该模块", constraint_context)

    def test_unknown_role_returns_empty_contexts(self) -> None:
        contexts = build_module_contexts_from_markdown(
            "业务变更文档",
            max_chars=10000,
            modules=[],
            document_role=UNKNOWN,
        )

        self.assertEqual(contexts, {})

    def test_renders_product_level_debug_markdown(self) -> None:
        markdown = render_product_module_contexts_debug(
            product_folder="测试产品目录",
            document_items=[
                {
                    "filename": "1 产品介绍.docx",
                    "role": PRODUCT_INTRO,
                    "normalized_name": "产品介绍",
                    "target_modules": ["supplementary_info.product_intro"],
                    "markdown_chars": 12,
                    "module_contexts": {"supplementary_info.product_intro": "产品介绍正文"},
                },
                {
                    "filename": "4 申请表.doc",
                    "role": APPLICATION_FORM,
                    "normalized_name": "申请表",
                    "target_modules": ["application_form_info.document_info"],
                    "error": "UnsupportedFormatException",
                },
            ],
        )

        self.assertIn("# 产品目录切块预览：测试产品目录", markdown)
        self.assertIn("## 文件：1 产品介绍.docx", markdown)
        self.assertIn("### supplementary_info.product_intro", markdown)
        self.assertIn("产品介绍正文", markdown)
        self.assertIn("### 转换失败", markdown)

    def test_generates_real_product_context_debug_files(self) -> None:
        """扫描真实测试产品目录，并生成产品级切块 Markdown 供人工审查。"""

        raw_root = Path(r"E:\GitHub\产品数据")
        if not raw_root.exists():
            self.skipTest(f"真实产品数据目录不存在：{raw_root}")

        output_dir = Path("data/product_doc_agent/debug/context_splitter_test_output")
        output_dir.mkdir(parents=True, exist_ok=True)
        loader = DocumentLoader()
        renderer = MarkdownRenderer(enable_ocr_flow=False)
        by_dir: dict[Path, list[tuple[Path, object]]] = defaultdict(list)

        for path in sorted(raw_root.rglob("*")):
            if not path.is_file():
                continue
            classification = classify_document(path)
            if classification.role == UNKNOWN:
                continue
            by_dir[path.parent].append((path, classification))

        written_paths: list[Path] = []
        for folder, items in sorted(by_dir.items(), key=lambda item: str(item[0])):
            document_items: list[dict[str, object]] = []
            for path, classification in items:
                item: dict[str, object] = {
                    "filename": path.name,
                    "source_file": str(path),
                    "role": classification.role,
                    "normalized_name": classification.normalized_name,
                    "target_modules": list(classification.target_modules),
                }
                try:
                    render_path = prepare_test_render_path(path, raw_root)
                    loaded_document = loader.load(render_path)
                    markdown = renderer.render_document_result(loaded_document).markdown
                    item["markdown_chars"] = len(markdown)
                    if render_path != path:
                        item["converted_source_file"] = str(render_path)
                    item["module_contexts"] = build_module_contexts_from_markdown(
                        markdown,
                        max_chars=12000,
                        modules=classification.target_modules,
                        document_role=classification.role,
                    )
                except Exception as exc:
                    item["error"] = f"{exc.__class__.__name__}: {exc}"
                document_items.append(item)

            output_path = output_dir / f"{safe_test_output_name(folder.relative_to(raw_root))}_module_contexts.md"
            output_path.write_text(
                render_product_module_contexts_debug(
                    product_folder=folder,
                    document_items=document_items,
                ),
                encoding="utf-8",
            )
            written_paths.append(output_path)

        self.assertEqual(len(written_paths), 2)
        for output_path in written_paths:
            text = output_path.read_text(encoding="utf-8")
            self.assertIn("# 产品目录切块预览：", text)
            self.assertIn("## 文件：", text)
            self.assertIn("### supplementary_info.", text)


if __name__ == "__main__":
    unittest.main()


def safe_test_output_name(path: Path) -> str:
    """把产品目录相对路径转换成测试输出文件名。"""

    raw_name = "_".join(path.parts)
    safe_name = "".join(char if char.isalnum() or char in "-_." else "_" for char in raw_name)
    return safe_name[:120] or "product"


def prepare_test_render_path(path: Path, raw_root: Path) -> Path:
    """测试预览遇到老 .doc 时，复用现有 Word COM 转 docx 能力。"""

    if path.suffix.lower() != ".doc":
        return path
    task = RawDocumentTask(source_path=path, raw_root=raw_root)
    return convert_doc_to_docx_with_word(task, Path("data/product_doc_agent"))
