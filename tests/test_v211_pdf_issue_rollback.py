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
from pdf_pipeline import (
    embed_document_pdf,
)


class PDFIssueRollbackTests(unittest.TestCase):
    """Failure-injection tests for PDF issuance transaction."""

    def setUp(self):

        self.temp_dir = (
            tempfile.TemporaryDirectory()
        )

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
        # render_pdf_pages()会被mock，
        # 所以源PDF只需要稳定存在并可计算SHA256。
        # ----------------------------------------------------

        self.input_pdf.write_bytes(
            b"%PDF-1.4\n"
            b"% V2.1.1 PDF rollback test\n"
        )

        # ----------------------------------------------------
        # Carrier仍然需要读取真实页面图像。
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
            str(
                self.rendered_page
            ),
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

    # ========================================================
    # Helpers
    # ========================================================

    def _embed(
        self,
        token,
        watermark_number,
    ):
        # ----------------------------------------------------
        # PDF renderer可以返回外部临时页面。
        #
        # _prepare_pdf_reference_set()会把它复制到自己拥有的
        # Canonical staging目录，再发布reference_pages。
        # ----------------------------------------------------

        with patch(
            "pdf_pipeline.render_pdf_pages",
            return_value=(
                [
                    self.rendered_page
                ],
                "",
            ),
        ):
            return embed_document_pdf(
                input_pdf=
                    self.input_pdf,

                registry_path=
                    self.registry_path,

                key=
                    "TEST_PDF_ROLLBACK_KEY",

                output_pdf=
                    self.output_pdf,

                assets_root=
                    self.assets_root,

                dpi=
                    96,

                alpha=
                    20.0,

                repeat=
                    1,

                pilot_bits=
                    16,

                pilot_repeat=
                    1,

                pilot_alpha=
                    20.0,

                recipient=
                    "unit_test",

                session=
                    "pdf_rollback_test",

                notes=
                    "V2.1.1-F2-B2-Test",

                trace_token=
                    token,

                watermark_number=
                    watermark_number,

                source_name=
                    "source.pdf",
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

    def _output_tmp_path(
        self,
    ):
        return self.output_pdf.with_suffix(
            ".tmp.pdf"
        )

    def _output_backup_path(
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

    @staticmethod
    def _successful_pdf_save(
        image_paths,
        output_pdf,
        dpi=150,
    ):
        # ----------------------------------------------------
        # Transaction测试不需要生成真实PDF结构。
        #
        # 这里只模拟：
        # _save_raster_pdf()成功后最终文件已经存在。
        # ----------------------------------------------------

        Path(
            output_pdf
        ).write_bytes(
            b"NEW_WATERMARKED_PDF"
        )

    # ========================================================
    # 1. Carrier失败
    # ========================================================

    def test_carrier_failure_rolls_back_issue_and_issue_directory(
        self,
    ):
        token = (
            "1111111111111111"
        )

        with patch(
            "document_carrier."
            "embed_bits_adaptive",

            side_effect=
                RuntimeError(
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

    # ========================================================
    # 2. PDF写入失败
    #
    # 旧output必须恢复，
    # 本次.tmp必须删除。
    # ========================================================

    def test_pdf_write_failure_restores_old_output_and_removes_tmp(
        self,
    ):
        token = (
            "2222222222222222"
        )

        old_output = (
            b"OLD_EXISTING_PDF"
        )

        self.output_pdf.write_bytes(
            old_output
        )

        def failing_pdf_save(
            image_paths,
            output_pdf,
            dpi=150,
        ):
            output_pdf = Path(
                output_pdf
            )

            temporary = (
                output_pdf.with_suffix(
                    ".tmp.pdf"
                )
            )

            temporary.write_bytes(
                b"PARTIAL_PDF_TMP"
            )

            raise RuntimeError(
                "forced PDF write failure"
            )

        with patch(
            "pdf_pipeline._save_raster_pdf",
            side_effect=
                failing_pdf_save,
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

        self.assertFalse(
            self._output_tmp_path().exists()
        )

        self.assertFalse(
            self._output_backup_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_path(
                token
            ).exists()
        )

    # ========================================================
    # 3. Manifest写入失败
    #
    # 新PDF已经产生，
    # 但Manifest没有完成。
    #
    # 必须恢复旧PDF并删除Manifest.tmp。
    # ========================================================

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

        def failing_manifest_write(
            path,
            data,
        ):
            path = Path(
                path
            )

            temporary = (
                path.with_suffix(
                    path.suffix
                    + ".tmp"
                )
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
                "forced PDF manifest failure"
            )

        with patch(
            "pdf_pipeline._save_raster_pdf",
            side_effect=
                self._successful_pdf_save,
        ):

            with patch(
                "pdf_pipeline._write_json",
                side_effect=
                    failing_manifest_write,
            ):

                with self.assertRaisesRegex(
                    RuntimeError,
                    "forced PDF manifest failure",
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

        self.assertEqual(
            old_output,
            self.output_pdf.read_bytes(),
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
            self._output_tmp_path().exists()
        )

        self.assertFalse(
            self._output_backup_path(
                token
            ).exists()
        )

    # ========================================================
    # 4. attach失败
    #
    # output + Manifest都已经生成，
    # 但Registry COMMIT失败。
    #
    # 新发行资产必须全部撤销。
    # ========================================================

    def test_attach_failure_removes_new_output_manifest_and_issue_assets(
        self,
    ):
        token = (
            "4444444444444444"
        )

        with patch(
            "pdf_pipeline._save_raster_pdf",
            side_effect=
                self._successful_pdf_save,
        ):

            with patch(
                "pdf_pipeline."
                "attach_issue_artifact",

                side_effect=
                    RuntimeError(
                        "forced PDF attach failure"
                    ),
            ):

                with self.assertRaisesRegex(
                    RuntimeError,
                    "forced PDF attach failure",
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
            self._output_tmp_path().exists()
        )

        self.assertFalse(
            self._output_backup_path(
                token
            ).exists()
        )

    # ========================================================
    # 5. rollback执行器自身失败
    #
    # 二次异常绝不能覆盖真正的发行异常。
    # ========================================================

    def test_rollback_failure_does_not_replace_original_exception(
        self,
    ):
        token = (
            "5555555555555555"
        )

        with patch(
            "pdf_pipeline._save_raster_pdf",

            side_effect=
                RuntimeError(
                    "ORIGINAL_PDF_FAILURE"
                ),
        ):

            with patch(
                "pdf_pipeline."
                "_rollback_pdf_issue_failure",

                side_effect=
                    RuntimeError(
                        "SECONDARY_PDF_"
                        "ROLLBACK_FAILURE"
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
                (
                    "SECONDARY_PDF_"
                    "ROLLBACK_FAILURE"
                )
                in message

                for message
                in diagnostics
            )
        )

    # ========================================================
    # 6. 正常COMMIT
    # ========================================================

    def test_successful_issue_commits_artifacts_without_rollback(
        self,
    ):
        token = (
            "6666666666666666"
        )

        watermark_number = (
            "PDF_COMMIT_001"
        )

        # ----------------------------------------------------
        # 同时验证：
        # 已有旧output时，成功COMMIT后backup会被清理。
        # ----------------------------------------------------

        self.output_pdf.write_bytes(
            b"OLD_PDF_TO_REPLACE"
        )

        with patch(
            "pdf_pipeline._save_raster_pdf",
            side_effect=
                self._successful_pdf_save,
        ):

            (
                manifest_path,
                manifest,
            ) = self._embed(
                token,
                watermark_number,
            )

        manifest_path = Path(
            manifest_path
        )

        self.assertTrue(
            self.output_pdf.is_file()
        )

        self.assertEqual(
            b"NEW_WATERMARKED_PDF",
            self.output_pdf.read_bytes(),
        )

        self.assertTrue(
            manifest_path.is_file()
        )

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

        # ----------------------------------------------------
        # issue页面在成功COMMIT后必须保留。
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Registry artifact完整。
        # ----------------------------------------------------

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

        self.assertEqual(
            str(
                self.output_pdf.resolve()
            ),
            issue.get(
                "output_path"
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

        self.assertEqual(
            sha256_file(
                self.output_pdf
            ),
            issue.get(
                "output_sha256"
            ),
        )

        # ----------------------------------------------------
        # 所有事务临时文件都应该消失。
        # ----------------------------------------------------

        self.assertFalse(
            self._output_tmp_path().exists()
        )

        self.assertFalse(
            self._output_backup_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_tmp_path(
                token
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()