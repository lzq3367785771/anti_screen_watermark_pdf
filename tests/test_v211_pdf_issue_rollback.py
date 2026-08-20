import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from document_registry import (
    load_document_registry,
    sha256_file,
)
from pdf_pipeline import embed_document_pdf


class PDFIssueRollbackTests(unittest.TestCase):
    """Failure-injection tests for PDF issuance rollback."""

    def setUp(self):
        # ----------------------------------------------------
        # 每个测试都在独立临时目录中运行。
        #
        # 不会碰真实：
        # document_registry.json
        # document_assets
        # Web输出目录
        # ----------------------------------------------------

        self.temp_dir = tempfile.TemporaryDirectory()

        self.root = Path(
            self.temp_dir.name
        )

        self.input_pdf = (
            self.root
            / "source.pdf"
        )

        self.registry_path = (
            self.root
            / "document_registry.json"
        )

        self.assets_root = (
            self.root
            / "assets"
        )

        self.output_pdf = (
            self.root
            / "watermarked.pdf"
        )

        self.rendered_page = (
            self.root
            / "rendered_page.png"
        )

        # ----------------------------------------------------
        # embed_document_pdf() 在本测试中不会真正调用
        # Poppler，因为 render_pdf_pages() 会被 patch。
        #
        # 因此这里只需要一个稳定存在、可参与 SHA256 的
        # 源文件即可。
        # ----------------------------------------------------

        self.input_pdf.write_bytes(
            b"%PDF-1.4\n"
            b"% V2.1.1 PDF rollback test\n"
        )

        # ----------------------------------------------------
        # 创建真实参考页面。
        #
        # 512 × 512 足够承载测试参数：
        #
        # 140 payload bits × repeat 1
        # +
        # 16 pilot bits × repeat 1
        # ----------------------------------------------------

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
            str(self.rendered_page),
            image,
        )

        self.assertTrue(
            written
        )

        self.document_id = (
            sha256_file(
                self.input_pdf
            )[:24]
        )

        self.document_assets = (
            self.assets_root
            / self.document_id
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    # ----------------------------------------------------
    # Helpers
    # ----------------------------------------------------

    def _embed(
        self,
        token,
        watermark_number,
    ):
        # ------------------------------------------------
        # render_pdf_pages() 被替换为固定测试页面。
        #
        # 这样：
        #   不依赖 Poppler
        #   不启动外部进程
        #   测试速度快且确定
        # ------------------------------------------------

        with patch(
            "pdf_pipeline.render_pdf_pages",
            return_value=(
                [self.rendered_page],
                "",
            ),
        ):
            return embed_document_pdf(
                input_pdf=self.input_pdf,
                registry_path=self.registry_path,
                key="TEST_PDF_ROLLBACK_KEY",
                output_pdf=self.output_pdf,
                assets_root=self.assets_root,
                dpi=96,
                alpha=20.0,
                repeat=1,
                pilot_bits=16,
                pilot_repeat=1,
                pilot_alpha=20.0,
                recipient="unit_test",
                session="pdf_rollback_test",
                notes="V2.1.1-F2-B2-Test",
                trace_token=token,
                watermark_number=watermark_number,
                source_name="source.pdf",
            )

    def _issue_dir(
        self,
        token,
    ):
        return (
            self.document_assets
            / "issues"
            / token
        )

    def _manifest_path(
        self,
        token,
    ):
        return (
            self.document_assets
            / f"manifest_{token}.json"
        )

    def _manifest_tmp_path(
        self,
        token,
    ):
        manifest_path = (
            self._manifest_path(
                token
            )
        )

        return manifest_path.with_suffix(
            manifest_path.suffix
            + ".tmp"
        )

    def _backup_path(
        self,
        token,
    ):
        return self.output_pdf.with_name(
            self.output_pdf.name
            + "."
            + token
            + ".rollback.bak"
        )

    def _assert_registry_issue_removed(
        self,
        token,
    ):
        registry = load_document_registry(
            self.registry_path
        )

        self.assertNotIn(
            token,
            registry[
                "issues"
            ],
        )

    def _assert_issue_assets_removed(
        self,
        token,
    ):
        self.assertFalse(
            self._issue_dir(
                token
            ).exists()
        )

    # ----------------------------------------------------
    # 1.
    # Carrier内部已经创建issue，
    # 但随后页面嵌入失败。
    #
    # 预期：
    #   Registry issue删除
    #   issues/<token>/删除
    #   不产生output PDF
    # ----------------------------------------------------

    def test_carrier_failure_rolls_back_issue_and_issue_directory(
        self,
    ):
        token = (
            "1111111111111111"
        )

        with patch(
            "document_carrier.embed_bits_adaptive",
            side_effect=RuntimeError(
                "forced carrier failure"
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "forced carrier failure",
            ):
                self._embed(
                    token,
                    "PDF_ROLLBACK_001",
                )

        self._assert_registry_issue_removed(
            token
        )

        self._assert_issue_assets_removed(
            token
        )

        self.assertFalse(
            self.output_pdf.exists()
        )

        self.assertFalse(
            self._manifest_path(
                token
            ).exists()
        )

    # ----------------------------------------------------
    # 2.
    # PDF生成阶段失败。
    #
    # 同时模拟：
    #   output PDF原本已经存在
    #   _save_raster_pdf() 创建了.tmp.pdf后失败
    #
    # 预期：
    #   原output恢复
    #   新tmp删除
    #   Registry issue删除
    #   issue目录删除
    # ----------------------------------------------------

    def test_pdf_write_failure_restores_old_output_and_removes_tmp(
        self,
    ):
        token = (
            "2222222222222222"
        )

        old_output = (
            b"OLD_EXISTING_PDF_CONTENT"
        )

        self.output_pdf.write_bytes(
            old_output
        )

        def failing_pdf_write(
            page_images,
            output_pdf,
            dpi,
        ):
            output_pdf = Path(
                output_pdf
            )

            output_tmp = (
                output_pdf.with_suffix(
                    ".tmp.pdf"
                )
            )

            output_tmp.write_bytes(
                b"PARTIAL_NEW_PDF"
            )

            raise RuntimeError(
                "forced PDF write failure"
            )

        with patch(
            "pdf_pipeline._save_raster_pdf",
            side_effect=failing_pdf_write,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "forced PDF write failure",
            ):
                self._embed(
                    token,
                    "PDF_ROLLBACK_002",
                )

        self._assert_registry_issue_removed(
            token
        )

        self._assert_issue_assets_removed(
            token
        )

        self.assertTrue(
            self.output_pdf.is_file()
        )

        self.assertEqual(
            old_output,
            self.output_pdf.read_bytes(),
        )

        output_tmp = (
            self.output_pdf.with_suffix(
                ".tmp.pdf"
            )
        )

        self.assertFalse(
            output_tmp.exists()
        )

        self.assertFalse(
            self._backup_path(
                token
            ).exists()
        )

    # ----------------------------------------------------
    # 3.
    # PDF已经成功生成，
    # 但Manifest写入过程中失败。
    #
    # 这是之前Web真实出现过的问题所属阶段。
    #
    # 同时让_write_json()留下一个.json.tmp，
    # 验证rollback能够清理。
    # ----------------------------------------------------

    def test_manifest_write_failure_restores_old_output_and_removes_manifest_tmp(
        self,
    ):
        token = (
            "3333333333333333"
        )

        old_output = (
            b"OLD_PDF_BEFORE_MANIFEST_FAILURE"
        )

        self.output_pdf.write_bytes(
            old_output
        )

        def successful_pdf_write(
            page_images,
            output_pdf,
            dpi,
        ):
            Path(
                output_pdf
            ).write_bytes(
                b"NEW_WATERMARKED_PDF"
            )

        def failing_manifest_write(
            path,
            data,
        ):
            path = Path(
                path
            )

            temporary = path.with_suffix(
                path.suffix
                + ".tmp"
            )

            temporary.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            temporary.write_text(
                '{"partial": true',
                encoding="utf-8",
            )

            raise RuntimeError(
                "forced manifest write failure"
            )

        with patch(
            "pdf_pipeline._save_raster_pdf",
            side_effect=successful_pdf_write,
        ):
            with patch(
                "pdf_pipeline._write_json",
                side_effect=failing_manifest_write,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "forced manifest write failure",
                ):
                    self._embed(
                        token,
                        "PDF_ROLLBACK_003",
                    )

        self._assert_registry_issue_removed(
            token
        )

        self._assert_issue_assets_removed(
            token
        )

        # 原来的PDF必须恢复。
        self.assertTrue(
            self.output_pdf.is_file()
        )

        self.assertEqual(
            old_output,
            self.output_pdf.read_bytes(),
        )

        # 最终Manifest和临时Manifest都不能残留。
        self.assertFalse(
            self._manifest_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_tmp_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._backup_path(
                token
            ).exists()
        )

    # ----------------------------------------------------
    # 4.
    # output PDF和Manifest都已经成功生成，
    # 但最后attach_issue_artifact()失败。
    #
    # 预期：
    #   仍然不算提交
    #   所有本次发行资产回滚
    # ----------------------------------------------------

    def test_attach_failure_removes_new_output_manifest_and_issue_assets(
        self,
    ):
        token = (
            "4444444444444444"
        )

        def successful_pdf_write(
            page_images,
            output_pdf,
            dpi,
        ):
            Path(
                output_pdf
            ).write_bytes(
                b"NEW_WATERMARKED_PDF"
            )

        with patch(
            "pdf_pipeline._save_raster_pdf",
            side_effect=successful_pdf_write,
        ):
            with patch(
                "pdf_pipeline.attach_issue_artifact",
                side_effect=RuntimeError(
                    "forced attach failure"
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "forced attach failure",
                ):
                    self._embed(
                        token,
                        "PDF_ROLLBACK_004",
                    )

        self._assert_registry_issue_removed(
            token
        )

        self._assert_issue_assets_removed(
            token
        )

        # 本次之前没有旧output，
        # 所以失败以后最终output也应该不存在。
        self.assertFalse(
            self.output_pdf.exists()
        )

        self.assertFalse(
            self._manifest_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_tmp_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._backup_path(
                token
            ).exists()
        )

    # ----------------------------------------------------
    # 5.
    # rollback执行器自身再发生异常，
    # 也绝不能覆盖真正导致发行失败的原始异常。
    #
    # 这是“保留原异常”契约。
    # ----------------------------------------------------

    def test_rollback_failure_does_not_replace_original_exception(
        self,
    ):
        token = (
            "5555555555555555"
        )

        with patch(
            "pdf_pipeline._save_raster_pdf",
            side_effect=RuntimeError(
                "ORIGINAL_PDF_FAILURE"
            ),
        ):
            with patch(
                "pdf_pipeline._rollback_pdf_issue_failure",
                side_effect=RuntimeError(
                    "SECONDARY_ROLLBACK_FAILURE"
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "ORIGINAL_PDF_FAILURE",
                ) as captured:
                    self._embed(
                        token,
                        "PDF_ROLLBACK_005",
                    )

        # 原异常仍然是PDF阶段错误，
        # 而不是rollback错误。
        self.assertEqual(
            "ORIGINAL_PDF_FAILURE",
            str(
                captured.exception
            ),
        )

        diagnostics = getattr(
            captured.exception,
            "rollback_diagnostics",
            [],
        )

        self.assertTrue(
            diagnostics
        )

        self.assertTrue(
            any(
                "SECONDARY_ROLLBACK_FAILURE"
                in message
                for message in diagnostics
            )
        )


    # ----------------------------------------------------
    # 6.
    # 正常成功发行：
    #
    # attach_issue_artifact() 成功以后即视为 COMMIT。
    #
    # 预期：
    #   Registry issue保留
    #   artifact字段完整
    #   issue页面目录保留
    #   output PDF保留
    #   Manifest保留
    #   rollback backup不存在
    #   所有临时文件不存在
    # ----------------------------------------------------

    def test_successful_issue_commits_artifacts_without_rollback(
        self,
    ):
        token = (
            "6666666666666666"
        )

        watermark_number = (
            "PDF_COMMIT_001"
        )

        manifest_path, manifest = (
            self._embed(
                token,
                watermark_number,
            )
        )

        manifest_path = Path(
            manifest_path
        )

        # ------------------------------------------------
        # 1. 返回结果必须属于本次发行。
        # ------------------------------------------------

        self.assertEqual(
            token,
            manifest[
                "trace_token"
            ],
        )

        self.assertEqual(
            watermark_number,
            manifest[
                "watermark_number"
            ],
        )

        self.assertEqual(
            self._manifest_path(
                token
            ).resolve(),
            manifest_path.resolve(),
        )

        # ------------------------------------------------
        # 2. 最终输出文件必须存在。
        # ------------------------------------------------

        self.assertTrue(
            self.output_pdf.is_file()
        )

        self.assertGreater(
            self.output_pdf.stat().st_size,
            0,
        )

        self.assertTrue(
            manifest_path.is_file()
        )

        self.assertGreater(
            manifest_path.stat().st_size,
            0,
        )

        # ------------------------------------------------
        # 3. 本次issue页面目录必须保留。
        #
        # 成功提交以后这些页面属于正式发行资产，
        # 绝不能被rollback删除。
        # ------------------------------------------------

        issue_dir = (
            self._issue_dir(
                token
            )
        )

        self.assertTrue(
            issue_dir.is_dir()
        )

        watermarked_pages = list(
            issue_dir.glob(
                "page_*_watermarked.png"
            )
        )

        self.assertTrue(
            watermarked_pages
        )

        # ------------------------------------------------
        # 4. Registry issue必须仍然存在。
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

        self.assertEqual(
            watermark_number,
            issue.get(
                "watermark_number"
            ),
        )

        # ------------------------------------------------
        # 5. attach_issue_artifact() 必须已经真正写入
        #    artifact元数据。
        # ------------------------------------------------

        self.assertEqual(
            str(
                self.output_pdf.resolve()
            ),
            issue.get(
                "output_path"
            ),
        )

        self.assertEqual(
            str(
                self.output_pdf.resolve()
            ),
            issue.get(
                "output_pdf"
            ),
        )

        self.assertEqual(
            "pdf",
            issue.get(
                "output_type"
            ),
        )

        self.assertEqual(
            str(
                manifest_path.resolve()
            ),
            issue.get(
                "manifest_path"
            ),
        )

        self.assertTrue(
            issue.get(
                "output_sha256"
            )
        )

        self.assertEqual(
            sha256_file(
                self.output_pdf
            ),
            issue.get(
                "output_sha256"
            ),
        )

        # ------------------------------------------------
        # 6. 成功发行以后不应残留事务临时文件。
        # ------------------------------------------------

        output_tmp = (
            self.output_pdf.with_suffix(
                ".tmp.pdf"
            )
        )

        self.assertFalse(
            output_tmp.exists()
        )

        self.assertFalse(
            self._manifest_tmp_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._backup_path(
                token
            ).exists()
        )



if __name__ == "__main__":
    unittest.main()