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
from pptx_pipeline import (
    embed_document_pptx,
)


class PPTXIssueRollbackTests(unittest.TestCase):
    """Failure-injection tests for PPTX issuance transaction."""

    def setUp(self):

        self.temp_dir = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temp_dir.name
        )

        self.input_pptx = (
            self.root
            / "source.pptx"
        )

        self.registry_path = (
            self.root
            / "document_registry.json"
        )

        self.assets_root = (
            self.root
            / "assets"
        )

        self.output_pptx = (
            self.root
            / "watermarked.pptx"
        )

        self.rendered_slide = (
            self.root
            / "rendered_slide.png"
        )

        # ----------------------------------------------------
        # 不启动真实PowerPoint。
        # 源文件只需要稳定存在并能计算SHA256。
        # ----------------------------------------------------

        self.input_pptx.write_bytes(
            b"FAKE_PPTX_SOURCE_FOR_"
            b"V2.1.1_ROLLBACK_TEST"
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
            str(
                self.rendered_slide
            ),
            image,
        )

        self.assertTrue(
            written
        )

        self.render_info = {
            "source_type":
                "pptx",

            "render_unit_type":
                "slide",

            "renderer":
                "fake_powerpoint",

            "dpi":
                96,

            "slide_count":
                1,

            "slide_width_points":
                384.0,

            "slide_height_points":
                384.0,

            "width":
                512,

            "height":
                512,
        }

        self.document_id = (
            sha256_file(
                self.input_pptx
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
        # 关键：
        #
        # 新版Canonical Reference要求renderer返回的Slide
        # 必须真实位于Pipeline提供的staging目录。
        #
        # 所以不能再直接return self.rendered_slide。
        # ----------------------------------------------------

        def fake_renderer(
            pptx_path,
            output_dir,
            dpi=96,
        ):
            output_dir = Path(
                output_dir
            )

            output_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            staged_slide = (
                output_dir
                / "slide_001.png"
            )

            staged_slide.write_bytes(
                self.rendered_slide.read_bytes()
            )

            render_info = dict(
                self.render_info
            )

            render_info[
                "dpi"
            ] = int(
                dpi
            )

            return (
                [
                    staged_slide
                ],
                render_info,
            )

        with patch(
            "pptx_pipeline.render_pptx_pages",
            side_effect=
                fake_renderer,
        ):
            return embed_document_pptx(
                input_pptx=
                    self.input_pptx,

                registry_path=
                    self.registry_path,

                key=
                    "TEST_PPTX_ROLLBACK_KEY",

                output_pptx=
                    self.output_pptx,

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
                    "pptx_rollback_test",

                notes=
                    "V2.1.1-F3-A3-Test",

                trace_token=
                    token,

                watermark_number=
                    watermark_number,

                source_name=
                    "source.pptx",
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
    ):
        return (
            self.output_pptx
            .with_suffix(
                ".manifest.json"
            )
        )

    def _manifest_tmp_path(
        self,
    ):
        manifest_path = (
            self._manifest_path()
        )

        return (
            manifest_path.with_suffix(
                manifest_path.suffix
                + ".tmp"
            )
        )

    def _staging_path(
        self,
        token,
    ):
        return (
            self.output_pptx
            .with_name(
                self.output_pptx.name
                + "."
                + token
                + ".staging.pptx"
            )
        )

    def _output_backup_path(
        self,
        token,
    ):
        return (
            self.output_pptx
            .with_name(
                self.output_pptx.name
                + "."
                + token
                + ".rollback.bak"
            )
        )

    def _manifest_backup_path(
        self,
        token,
    ):
        manifest_path = (
            self._manifest_path()
        )

        return (
            manifest_path.with_name(
                manifest_path.name
                + "."
                + token
                + ".rollback.bak"
            )
        )

    def _assert_registry_issue_removed(
        self,
        token,
    ):
        registry = (
            load_document_registry(
                self.registry_path
            )
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
    def _successful_rebuild(
        image_paths,
        output_pptx,
        slide_width_points,
        slide_height_points,
    ):
        Path(
            output_pptx
        ).write_bytes(
            b"NEW_FLATTENED_PPTX"
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
                    "PPTX_ROLLBACK_001",
                )

        self._assert_registry_issue_removed(
            token
        )

        self._assert_issue_assets_removed(
            token
        )

        self.assertFalse(
            self.output_pptx.exists()
        )

        self.assertFalse(
            self._staging_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_path().exists()
        )

    # ========================================================
    # 2. PPTX staging重建失败
    #
    # 此时旧final / 旧Manifest还没被移动。
    # ========================================================

    def test_staging_rebuild_failure_preserves_old_files_and_removes_staging(
        self,
    ):
        token = (
            "2222222222222222"
        )

        old_output = (
            b"OLD_EXISTING_PPTX"
        )

        old_manifest = (
            '{"old_manifest": true}'
        )

        self.output_pptx.write_bytes(
            old_output
        )

        self._manifest_path().write_text(
            old_manifest,
            encoding="utf-8",
        )

        def failing_rebuild(
            image_paths,
            output_pptx,
            slide_width_points,
            slide_height_points,
        ):
            staging = Path(
                output_pptx
            )

            staging.write_bytes(
                b"PARTIAL_STAGING_PPTX"
            )

            raise RuntimeError(
                "forced PPTX staging failure"
            )

        with patch(
            "pptx_pipeline."
            "rebuild_pptx_from_images",

            side_effect=
                failing_rebuild,
        ):

            with self.assertRaisesRegex(
                RuntimeError,
                "forced PPTX staging failure",
            ):
                self._embed(
                    token,
                    "PPTX_ROLLBACK_002",
                )

        self._assert_registry_issue_removed(
            token
        )

        self._assert_issue_assets_removed(
            token
        )

        self.assertEqual(
            old_output,
            self.output_pptx.read_bytes(),
        )

        self.assertEqual(
            old_manifest,
            self._manifest_path().read_text(
                encoding="utf-8",
            ),
        )

        self.assertFalse(
            self._staging_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._output_backup_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_backup_path(
                token
            ).exists()
        )

    # ========================================================
    # 3. Manifest写入失败
    #
    # 新PPTX已经发布，
    # 旧PPTX / Manifest必须恢复。
    # ========================================================

    def test_manifest_failure_restores_old_pptx_and_manifest(
        self,
    ):
        token = (
            "3333333333333333"
        )

        old_output = (
            b"OLD_PPTX_BEFORE_"
            b"MANIFEST_FAILURE"
        )

        old_manifest = (
            '{"version": "old"}'
        )

        self.output_pptx.write_bytes(
            old_output
        )

        self._manifest_path().write_text(
            old_manifest,
            encoding="utf-8",
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
                "forced PPTX manifest failure"
            )

        with patch(
            "pptx_pipeline."
            "rebuild_pptx_from_images",

            side_effect=
                self._successful_rebuild,
        ):

            with patch(
                "pptx_pipeline._write_json",

                side_effect=
                    failing_manifest_write,
            ):

                with self.assertRaisesRegex(
                    RuntimeError,
                    "forced PPTX manifest failure",
                ):
                    self._embed(
                        token,
                        "PPTX_ROLLBACK_003",
                    )

        self._assert_registry_issue_removed(
            token
        )

        self._assert_issue_assets_removed(
            token
        )

        self.assertEqual(
            old_output,
            self.output_pptx.read_bytes(),
        )

        self.assertEqual(
            old_manifest,
            self._manifest_path().read_text(
                encoding="utf-8",
            ),
        )

        self.assertFalse(
            self._manifest_tmp_path().exists()
        )

        self.assertFalse(
            self._staging_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._output_backup_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_backup_path(
                token
            ).exists()
        )

    # ========================================================
    # 4. attach失败
    #
    # 本次没有旧output / manifest，
    # 所有新发行资产都必须撤销。
    # ========================================================

    def test_attach_failure_removes_new_pptx_manifest_and_issue_assets(
        self,
    ):
        token = (
            "4444444444444444"
        )

        with patch(
            "pptx_pipeline."
            "rebuild_pptx_from_images",

            side_effect=
                self._successful_rebuild,
        ):

            with patch(
                "pptx_pipeline."
                "attach_issue_artifact",

                side_effect=
                    RuntimeError(
                        "forced PPTX attach failure"
                    ),
            ):

                with self.assertRaisesRegex(
                    RuntimeError,
                    "forced PPTX attach failure",
                ):
                    self._embed(
                        token,
                        "PPTX_ROLLBACK_004",
                    )

        self._assert_registry_issue_removed(
            token
        )

        self._assert_issue_assets_removed(
            token
        )

        self.assertFalse(
            self.output_pptx.exists()
        )

        self.assertFalse(
            self._manifest_path().exists()
        )

        self.assertFalse(
            self._manifest_tmp_path().exists()
        )

        self.assertFalse(
            self._staging_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._output_backup_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_backup_path(
                token
            ).exists()
        )

    # ========================================================
    # 5. rollback执行器自己失败
    # ========================================================

    def test_rollback_failure_does_not_replace_original_exception(
        self,
    ):
        token = (
            "5555555555555555"
        )

        with patch(
            "pptx_pipeline."
            "rebuild_pptx_from_images",

            side_effect=
                RuntimeError(
                    "ORIGINAL_PPTX_FAILURE"
                ),
        ):

            with patch(
                "pptx_pipeline."
                "_rollback_pptx_issue_failure",

                side_effect=
                    RuntimeError(
                        "SECONDARY_PPTX_"
                        "ROLLBACK_FAILURE"
                    ),
            ):

                with self.assertRaisesRegex(
                    RuntimeError,
                    "ORIGINAL_PPTX_FAILURE",
                ) as captured:

                    self._embed(
                        token,
                        "PPTX_ROLLBACK_005",
                    )

        self.assertEqual(
            "ORIGINAL_PPTX_FAILURE",
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
                    "SECONDARY_PPTX_"
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

    def test_successful_issue_commits_and_cleans_transaction_files(
        self,
    ):
        token = (
            "6666666666666666"
        )

        watermark_number = (
            "PPTX_COMMIT_001"
        )

        self.output_pptx.write_bytes(
            b"OLD_PPTX_TO_REPLACE"
        )

        self._manifest_path().write_text(
            '{"old": true}',
            encoding="utf-8",
        )

        with patch(
            "pptx_pipeline."
            "rebuild_pptx_from_images",

            side_effect=
                self._successful_rebuild,
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

        self.assertEqual(
            b"NEW_FLATTENED_PPTX",
            self.output_pptx.read_bytes(),
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
        # 成功issue页面必须保留。
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

        registry = (
            load_document_registry(
                self.registry_path
            )
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
                self.output_pptx.resolve()
            ),
            issue.get(
                "output_path"
            ),
        )

        self.assertEqual(
            "pptx",
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
                self.output_pptx
            ),
            issue.get(
                "output_sha256"
            ),
        )

        # ----------------------------------------------------
        # 事务临时文件全部消失。
        # ----------------------------------------------------

        self.assertFalse(
            self._staging_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._output_backup_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_backup_path(
                token
            ).exists()
        )

        self.assertFalse(
            self._manifest_tmp_path().exists()
        )


if __name__ == "__main__":
    unittest.main()