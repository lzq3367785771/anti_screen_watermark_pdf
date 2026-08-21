import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from document_carrier import (
    issue_watermarked_pages,
)
from document_registry import (
    key_id_for_key,
    load_document_registry,
    sha256_file,
)


class CarrierIssueContextTests(unittest.TestCase):
    """Tests for issue_watermarked_pages(issue_context=...)."""

    def setUp(self):
        # ----------------------------------------------------
        # 每个测试使用完全独立的临时目录。
        #
        # 不会读取或修改项目真实的：
        # document_registry.json
        # document_assets
        # ----------------------------------------------------

        self.temp_dir = tempfile.TemporaryDirectory()

        self.root = Path(
            self.temp_dir.name
        )

        self.registry_path = (
            self.root
            / "document_registry.json"
        )

        self.source_path = (
            self.root
            / "source.pdf"
        )

        self.document_assets = (
            self.root
            / "document_assets"
        )

        self.reference_dir = (
            self.document_assets
            / "reference_pages"
        )

        self.reference_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Carrier 本身只要求源文件存在，并通过 SHA256
        # 建立 document_id。
        #
        # 这里不需要生成真正可阅读的 PDF，
        # 因为本测试不经过 PDF Renderer。
        self.source_path.write_bytes(
            b"%PDF-1.4\n"
            b"% carrier issue context test\n"
        )

        # ----------------------------------------------------
        # 创建一张真实 PNG 参考页面。
        #
        # 512 × 512 足够容纳：
        # 140 bit payload
        # +
        # 少量 pilot
        # ----------------------------------------------------

        self.reference_path = (
            self.reference_dir
            / "page_001.png"
        )

        image = np.full(
            (
                512,
                512,
                3,
            ),
            180,
            dtype=np.uint8,
        )

        written = cv2.imwrite(
            str(self.reference_path),
            image,
        )

        self.assertTrue(
            written
        )

        self.page_records = [
            {
                "page_index": 1,
                "width": 512,
                "height": 512,
                "reference_path": str(
                    self.reference_path
                ),
                "reference_sha256": sha256_file(
                    self.reference_path
                ),
            }
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    # ----------------------------------------------------
    # Test helper
    # ----------------------------------------------------

    def _issue(
        self,
        issue_context,
        trace_token,
        watermark_number,
    ):
        return issue_watermarked_pages(
            source_path=self.source_path,
            registry_path=self.registry_path,
            page_records=self.page_records,
            dpi=96,
            media_box_points=[
                384.0,
                384.0,
            ],
            document_assets=self.document_assets,
            key="TEST_CARRIER_CONTEXT_KEY",
            key_id=key_id_for_key(
                "TEST_CARRIER_CONTEXT_KEY"
            ),
            alpha=20.0,
            repeat=1,
            pilot_bits=16,
            pilot_repeat=1,
            pilot_alpha=20.0,
            recipient="unit_test",
            session="carrier_context_test",
            notes="V2.1.1-F2-A-Test",
            trace_token=trace_token,
            watermark_number=watermark_number,
            source_name="source.pdf",
            source_type="pdf",
            render_unit_type="page",
            issue_context=issue_context,
        )

    # ----------------------------------------------------
    # 1.
    # 正常发行成功：
    #
    # issue_context 必须记录本次 issue 的所有权信息。
    # ----------------------------------------------------

    def test_successful_issue_populates_context(
        self,
    ):
        context = {}

        token = (
            "1111111111111111"
        )

        result = self._issue(
            issue_context=context,
            trace_token=token,
            watermark_number=(
                "CTX_SUCCESS_001"
            ),
        )

        self.assertTrue(
            context.get(
                "issue_created"
            )
        )

        self.assertEqual(
            token,
            context.get(
                "trace_token"
            ),
        )

        self.assertEqual(
            result[
                "document_id"
            ],
            context.get(
                "document_id"
            ),
        )

        self.assertEqual(
            result[
                "issue"
            ],
            context.get(
                "issue"
            ),
        )

        self.assertEqual(
            result[
                "issue_page_dir"
            ],
            context.get(
                "issue_page_dir"
            ),
        )

        self.assertTrue(
            context.get(
                "issue_page_dir_created"
            )
        )


        # ------------------------------------------------
        # Registry 中也必须确实存在同一个 issue。
        # ------------------------------------------------

        registry = load_document_registry(
            self.registry_path
        )

        self.assertIn(
            token,
            registry["issues"],
        )

        self.assertEqual(
            token,
            registry[
                "issues"
            ][token][
                "trace_token"
            ],
        )

    # ----------------------------------------------------
    # 2.
    # issue 已经创建，但后续页面嵌入失败：
    #
    # issue_watermarked_pages() 不会正常 return，
    # 但 issue_context 必须仍保存 token。
    #
    # 这是 F2-A 最核心的测试。
    # ----------------------------------------------------

    def test_embedding_failure_keeps_created_issue_in_context(
        self,
    ):
        context = {}

        token = (
            "2222222222222222"
        )

        with patch(
            "document_carrier.embed_bits_adaptive",
            side_effect=RuntimeError(
                "forced embedding failure"
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "forced embedding failure",
            ):
                self._issue(
                    issue_context=context,
                    trace_token=token,
                    watermark_number=(
                        "CTX_FAIL_001"
                    ),
                )

        # ------------------------------------------------
        # 虽然 Carrier 没有 return，
        # 但本次 issue 的身份不能丢失。
        # ------------------------------------------------

        self.assertTrue(
            context.get(
                "issue_created"
            )
        )

        self.assertEqual(
            token,
            context.get(
                "trace_token"
            ),
        )

        self.assertIn(
            "document_id",
            context,
        )

        self.assertIn(
            "issue",
            context,
        )

        self.assertIn(
            "issue_page_dir",
            context,
        )

        self.assertTrue(
            context.get(
                "issue_page_dir_created"
            )
        )

        expected_issue_dir = (
            self.document_assets
            / "issues"
            / token
        ).resolve()

        actual_issue_dir = Path(
            context[
                "issue_page_dir"
            ]
        ).resolve()

        self.assertEqual(
            expected_issue_dir,
            actual_issue_dir,
        )

        # ------------------------------------------------
        # Registry 中此时仍然存在这个 unattached issue。
        #
        # F2-B 以后正是利用 context["trace_token"]
        # 调 rollback_unattached_issue() 删除它。
        # ------------------------------------------------

        registry = load_document_registry(
            self.registry_path
        )

        self.assertIn(
            token,
            registry["issues"],
        )

        issue = registry[
            "issues"
        ][token]

        self.assertEqual(
            "issued",
            issue.get(
                "status"
            ),
        )

        # 尚未进入 attach_issue_artifact()，
        # 因而不应该出现最终 artifact 字段。
        artifact_fields = (
            "output_path",
            "output_pdf",
            "output_sha256",
            "output_type",
            "manifest_path",
        )

        for field in artifact_fields:
            self.assertFalse(
                issue.get(
                    field
                ),
                msg=(
                    f"异常发行不应提前写入"
                    f" artifact 字段: {field}"
                ),
            )

    # ----------------------------------------------------
    # 3.
    # issue_document_trace() 自己失败：
    #
    # 本次调用根本没有成功创建 issue，
    # 因此 context 必须保持空。
    #
    # 同时验证旧 context 会在新调用开始时被 clear()。
    # ----------------------------------------------------

    def test_issue_creation_failure_leaves_context_empty(
        self,
    ):
        context = {
            "issue_created": True,
            "trace_token": (
                "ffffffffffffffff"
            ),
            "document_id": (
                "stale_document"
            ),
            "issue_page_dir": (
                "stale_issue_dir"
            ),
        }

        with patch(
            "document_carrier.issue_document_trace",
            side_effect=ValueError(
                "forced issue creation failure"
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "forced issue creation failure",
            ):
                self._issue(
                    issue_context=context,
                    trace_token=(
                        "3333333333333333"
                    ),
                    watermark_number=(
                        "CTX_FAIL_002"
                    ),
                )

        # ------------------------------------------------
        # 最关键：
        #
        # 不能残留旧 token。
        #
        # 否则未来异常处理可能错误回滚
        # 上一次发行。
        # ------------------------------------------------

        self.assertEqual(
            {},
            context,
        )

        registry = load_document_registry(
            self.registry_path
        )

        self.assertEqual(
            {},
            registry[
                "issues"
            ],
        )


    # ----------------------------------------------------
    # 4.
    # token 专属目录在本次发行前已经存在：
    #
    # Carrier 必须拒绝继续使用旧目录，
    # 并明确告诉上层：
    #
    #   issue 已由本次调用创建
    #   但 issue 页面目录不属于本次调用
    #
    # 因此未来 rollback 只能删除 Registry issue，
    # 不能删除这个旧目录。
    # ----------------------------------------------------

    def test_preexisting_issue_directory_is_not_owned(
        self,
    ):
        context = {}

        token = (
            "4444444444444444"
        )

        issue_dir = (
            self.document_assets
            / "issues"
            / token
        )

        issue_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        marker_path = (
            issue_dir
            / "old_marker.txt"
        )

        marker_content = (
            "preexisting orphan asset"
        )

        marker_path.write_text(
            marker_content,
            encoding="utf-8",
        )

        files_before = sorted(
            path.name
            for path in issue_dir.iterdir()
        )

        with self.assertRaises(
            FileExistsError
        ):
            self._issue(
                issue_context=context,
                trace_token=token,
                watermark_number=(
                    "CTX_PREEXIST_001"
                ),
            )

        # ------------------------------------------------
        # issue_document_trace() 已经成功，
        # 所以本次调用确实拥有 Registry issue。
        # ------------------------------------------------

        self.assertTrue(
            context.get(
                "issue_created"
            )
        )

        self.assertEqual(
            token,
            context.get(
                "trace_token"
            ),
        )

        # ------------------------------------------------
        # 但是目录在调用前就存在，因此本次调用
        # 绝不能声称拥有这个目录。
        # ------------------------------------------------

        self.assertFalse(
            context.get(
                "issue_page_dir_created"
            )
        )

        actual_issue_dir = Path(
            context[
                "issue_page_dir"
            ]
        ).resolve()

        self.assertEqual(
            issue_dir.resolve(),
            actual_issue_dir,
        )

        # ------------------------------------------------
        # 原有目录和原有文件必须完全保留。
        # ------------------------------------------------

        self.assertTrue(
            issue_dir.is_dir()
        )

        self.assertTrue(
            marker_path.is_file()
        )

        self.assertEqual(
            marker_content,
            marker_path.read_text(
                encoding="utf-8",
            ),
        )

        files_after = sorted(
            path.name
            for path in issue_dir.iterdir()
        )

        self.assertEqual(
            files_before,
            files_after,
        )

        # ------------------------------------------------
        # Registry issue 已经创建，但尚未 attach。
        #
        # 这正是以后 PDF Pipeline 应执行：
        #
        # rollback_unattached_issue(token)
        #
        # 但绝不能 rmtree(issue_dir) 的情况。
        # ------------------------------------------------

        registry = load_document_registry(
            self.registry_path
        )

        self.assertIn(
            token,
            registry[
                "issues"
            ],
        )

        issue = registry[
            "issues"
        ][token]

        self.assertEqual(
            "issued",
            issue.get(
                "status"
            ),
        )

        artifact_fields = (
            "output_path",
            "output_pdf",
            "output_sha256",
            "output_type",
            "manifest_path",
        )

        for field in artifact_fields:
            self.assertFalse(
                issue.get(
                    field
                )
            )



if __name__ == "__main__":
    unittest.main()
