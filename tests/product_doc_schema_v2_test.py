from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.product_doc_agent.document_classifier import classify_document
from agent.product_doc_agent.llm_extractor import EXTRACTION_MODULES, ProductDocumentLLMExtractor, modules_for_document
from agent.product_doc_agent.workflow import ProductDocAgentWorkflow
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


class ProductDocConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_slow_module_cache_reuses_exact_prompt_result(self) -> None:
        module_name = "application_form_info.agreement_rules"
        context = {module_name: "协议期内退订需按规则处理。"}
        output = [
            {
                "rule_type": "termination",
                "description": "协议期内退订需按规则处理。",
                "severity": "medium",
                "applies_to": ["客户"],
                "obligation_party": "客户",
                "conditions": ["协议期内退订"],
                "consequence": "按规则处理",
                "raw_text": "协议期内退订需按规则处理。",
            }
        ]
        llm = SimpleNamespace(
            model_name="test-model",
            openai_api_base="https://example.test",
            extra_body={"thinking": {"type": "disabled"}},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            first = ProductDocumentLLMExtractor(llm=llm, max_concurrency=1)
            first.module_cache_dir = Path(temp_dir)
            call_count = 0

            async def fake_invoke(prompt, *, expected_module):
                nonlocal call_count
                call_count += 1
                return output

            first._ainvoke_json = fake_invoke
            first_result = await first._extract_one_module_async(
                module_name,
                context,
                continue_on_error=False,
            )

            second = ProductDocumentLLMExtractor(llm=llm, max_concurrency=1)
            second.module_cache_dir = Path(temp_dir)

            async def fail_if_called(prompt, *, expected_module):
                raise AssertionError("cache miss unexpectedly called LLM")

            second._ainvoke_json = fail_if_called
            second_result = await second._extract_one_module_async(
                module_name,
                context,
                continue_on_error=False,
            )

        self.assertEqual(call_count, 1)
        self.assertEqual(first_result[1], output)
        self.assertEqual(second_result[1], output)

    def test_module_cache_strips_source_file_before_reuse(self) -> None:
        module_name = "application_form_info.agreement_rules"
        output = [
            {
                "rule_type": "restriction",
                "description": "仅限指定客户办理。",
                "severity": "high",
                "applies_to": ["客户"],
                "obligation_party": "客户",
                "conditions": ["指定客户"],
                "consequence": "不符合则不能办理",
                "raw_text": "仅限指定客户办理。",
                "source_file": "old.docx",
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            extractor = ProductDocumentLLMExtractor(llm=object(), max_concurrency=1)
            extractor.module_cache_dir = Path(temp_dir)
            cache_key = extractor._module_cache_key(module_name, "same prompt")
            extractor._write_module_cache(module_name, cache_key, output)
            cached = extractor._read_module_cache(module_name, cache_key)

        self.assertNotIn("source_file", cached[0])

    async def test_shared_semaphore_limits_concurrent_module_batches(self) -> None:
        extractor = ProductDocumentLLMExtractor(llm=object(), max_concurrency=4)
        active_count = 0
        peak_count = 0

        async def fake_extract(module_name, document_context, *, continue_on_error):
            nonlocal active_count, peak_count
            active_count += 1
            peak_count = max(peak_count, active_count)
            await asyncio.sleep(0.01)
            active_count -= 1
            return module_name, {}, None

        extractor._extract_one_module_async = fake_extract
        shared_semaphore = asyncio.Semaphore(4)
        modules = EXTRACTION_MODULES[:5]

        await asyncio.gather(
            extractor.extract_modules_async("first", modules=modules, request_semaphore=shared_semaphore),
            extractor.extract_modules_async("second", modules=modules, request_semaphore=shared_semaphore),
        )

        self.assertEqual(peak_count, 4)

    async def test_product_folder_limits_file_and_module_concurrency_to_four(self) -> None:
        class FakeLoader:
            async def load_async(self, path):
                await asyncio.sleep(0.01)
                return SimpleNamespace(source_path=str(path), filename=Path(path).name)

        class FakeRenderer:
            def render_document_result(self, loaded_document):
                return SimpleNamespace(markdown="# Test\n\nCustomer name and pricing information")

        class FakeExtractor:
            max_context_chars = 12000
            max_concurrency = 4

            def __init__(self):
                self.active_files = 0
                self.peak_files = 0
                self.active_requests = 0
                self.peak_requests = 0

            async def extract_modules_async(
                self,
                document_context,
                modules,
                *,
                continue_on_error,
                request_semaphore,
            ):
                self.active_files += 1
                self.peak_files = max(self.peak_files, self.active_files)

                async def extract_one(module_name):
                    async with request_semaphore:
                        self.active_requests += 1
                        self.peak_requests = max(self.peak_requests, self.active_requests)
                        await asyncio.sleep(0.02)
                        self.active_requests -= 1
                    return module_name, get_module_output_template(module_name)

                results = await asyncio.gather(*(extract_one(module_name) for module_name in modules))
                self.active_files -= 1
                return dict(results)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            product_folder = root / "product"
            product_folder.mkdir()
            for filename in (
                "1 产品介绍.docx",
                "2 资费表.xlsx",
                "3 关键字.docx",
                "4 申请表_测试.docx",
                "4-2 申请手续提示.txt",
            ):
                (product_folder / filename).write_text("test", encoding="utf-8")

            workflow = ProductDocAgentWorkflow(
                data_root=root / "output",
                llm=object(),
                max_concurrency=4,
                max_file_concurrency=4,
                enable_debug_markdown=False,
                enable_ocr_flow=False,
            )
            fake_extractor = FakeExtractor()
            workflow.loader = FakeLoader()
            workflow.markdown_renderer = FakeRenderer()
            workflow.extractor = fake_extractor

            result = await workflow.run_product_folder_async(product_folder)

        self.assertEqual(result.document_count, 5)
        self.assertEqual(result.module_count, 9)
        self.assertEqual(fake_extractor.peak_files, 4)
        self.assertEqual(fake_extractor.peak_requests, 4)


if __name__ == "__main__":
    unittest.main()
