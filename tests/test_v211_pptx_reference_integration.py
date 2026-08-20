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


class PPTXReferenceIntegrationTests(
    unittest.TestCase
):

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

        # 测试中不会启动真实PowerPoint。
        # 文件只需要稳定存在并可计算SHA256。
        self.input_pptx.write_bytes(
            b"FAKE_PPTX_SOURCE_FOR_"
            b"CANONICAL_INTEGRATION_TEST"
        )

        self.registry_path = (
            self.root
            / "document_registry.json"
        )

        self.assets_root = (
            self.root
            / "assets"
        )

        self.document_id = (
            sha256_file(
                self.input_pptx
            )[:24]
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    # ========================================================
    # Helpers
    # ========================================================

    @staticmethod
    def _fake_renderer(
        pptx_path,
        output_dir,
        dpi=96,
    ):
        """Simulate PowerPoint rendering into the supplied staging dir."""

        output_dir = Path(
            output_dir
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        slide_path = (
            output_dir
            / "slide_001.png"
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
                slide_path
            ),
            image,
        )

        if not written:
            raise RuntimeError(
                "测试Slide写入失败"
            )

        return (
            [
                slide_path
            ],
            {
                "source_type":
                    "pptx",

                "render_unit_type":
                    "slide",

                "renderer":
                    "fake_powerpoint",

                "dpi":
                    int(dpi),

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
            },
        )

    @staticmethod
    def _fake_rebuild(
        image_paths,
        output_pptx,
        slide_width_points,
        slide_height_points,
    ):
        """Simulate flattened PPTX creation without PowerPoint COM."""

        Path(
            output_pptx
        ).write_bytes(
            b"FAKE_FLATTENED_WATERMARKED_PPTX"
        )

    def _issue(
        self,
        output_pptx,
        token,
        watermark_number,
        dpi=96,
    ):
        return embed_document_pptx(
            input_pptx=
                self.input_pptx,

            registry_path=
                self.registry_path,

            key=
                "TEST_PPTX_CANONICAL_KEY",

            output_pptx=
                output_pptx,

            assets_root=
                self.assets_root,

            dpi=
                dpi,

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
                "pptx_canonical_integration",

            notes=
                "V2.1.1-F4-B2-Test",

            trace_token=
                token,

            watermark_number=
                watermark_number,

            source_name=
                "source.pptx",
        )

    # ========================================================
    # 1.
    # 第一次发行建立Canonical。
    #
    # 第二次同PPTX / 同DPI：
    # render_pptx_pages()必须完全不再执行。
    # ========================================================

    def test_second_issue_reuses_canonical_reference_without_rendering(
        self,
    ):

        token_1 = (
            "1111111111111111"
        )

        token_2 = (
            "2222222222222222"
        )

        output_1 = (
            self.root
            / "watermarked_1.pptx"
        )

        output_2 = (
            self.root
            / "watermarked_2.pptx"
        )

        # ----------------------------------------------------
        # 第一次发行：
        # renderer必须执行一次，用来建立Canonical。
        # ----------------------------------------------------

        with patch(
            "pptx_pipeline.render_pptx_pages",
            side_effect=
                self._fake_renderer,
        ) as first_renderer:

            with patch(
                "pptx_pipeline."
                "rebuild_pptx_from_images",

                side_effect=
                    self._fake_rebuild,
            ):

                (
                    manifest_path_1,
                    manifest_1,
                ) = self._issue(
                    output_pptx=
                        output_1,

                    token=
                        token_1,

                    watermark_number=
                        "PPTX_CANONICAL_001",

                    dpi=
                        96,
                )

        self.assertEqual(
            1,
            first_renderer.call_count,
        )

        self.assertTrue(
            output_1.is_file()
        )

        self.assertTrue(
            Path(
                manifest_path_1
            ).is_file()
        )

        self.assertEqual(
            token_1,
            manifest_1[
                "trace_token"
            ],
        )

        self.assertEqual(
            "fake_powerpoint",
            manifest_1[
                "rendering"
            ][
                "renderer"
            ],
        )

        # ----------------------------------------------------
        # 记录第一次发行后的Canonical状态。
        # ----------------------------------------------------

        registry_after_first = (
            load_document_registry(
                self.registry_path
            )
        )

        document_after_first = (
            registry_after_first[
                "documents"
            ][
                self.document_id
            ]
        )

        first_pages = [
            dict(page)
            for page
            in document_after_first[
                "pages"
            ]
        ]

        self.assertEqual(
            1,
            len(
                first_pages
            ),
        )

        first_reference_path = Path(
            first_pages[0][
                "reference_path"
            ]
        )

        first_reference_sha256 = (
            first_pages[0][
                "reference_sha256"
            ]
        )

        self.assertTrue(
            first_reference_path.is_file()
        )

        self.assertEqual(
            "slide_001.png",
            first_reference_path.name,
        )

        # ----------------------------------------------------
        # 第二次发行：
        #
        # render_pptx_pages一旦被调用，
        # 测试立即失败。
        #
        # rebuild仍然需要执行，因为每个Issue都要生成新的
        # flattened watermarked PPTX。
        # ----------------------------------------------------

        with patch(
            "pptx_pipeline.render_pptx_pages",

            side_effect=
                AssertionError(
                    "第二次PPTX发行不应该重新启动PowerPoint渲染"
                ),
        ) as second_renderer:

            with patch(
                "pptx_pipeline."
                "rebuild_pptx_from_images",

                side_effect=
                    self._fake_rebuild,
            ):

                (
                    manifest_path_2,
                    manifest_2,
                ) = self._issue(
                    output_pptx=
                        output_2,

                    token=
                        token_2,

                    watermark_number=
                        "PPTX_CANONICAL_002",

                    dpi=
                        96,
                )

        self.assertEqual(
            0,
            second_renderer.call_count,
        )

        self.assertTrue(
            output_2.is_file()
        )

        self.assertTrue(
            Path(
                manifest_path_2
            ).is_file()
        )

        self.assertEqual(
            token_2,
            manifest_2[
                "trace_token"
            ],
        )

        # ----------------------------------------------------
        # 第二次Manifest明确标记：
        # 本次采用Canonical复用，而非重新PowerPoint渲染。
        # ----------------------------------------------------

        self.assertEqual(
            "canonical_reference_reuse",
            manifest_2[
                "rendering"
            ][
                "renderer"
            ],
        )

        # ----------------------------------------------------
        # 两次发行：
        #
        # 同一个Document
        # 两个独立Issue
        # 同一套Canonical Reference
        # ----------------------------------------------------

        registry_after_second = (
            load_document_registry(
                self.registry_path
            )
        )

        self.assertIn(
            token_1,
            registry_after_second[
                "issues"
            ],
        )

        self.assertIn(
            token_2,
            registry_after_second[
                "issues"
            ],
        )

        document_after_second = (
            registry_after_second[
                "documents"
            ][
                self.document_id
            ]
        )

        second_pages = (
            document_after_second[
                "pages"
            ]
        )

        self.assertEqual(
            str(
                first_reference_path.resolve()
            ),
            second_pages[0][
                "reference_path"
            ],
        )

        self.assertEqual(
            first_reference_sha256,
            second_pages[0][
                "reference_sha256"
            ],
        )

        self.assertEqual(
            first_reference_sha256,
            sha256_file(
                first_reference_path
            ),
        )

    # ========================================================
    # 2.
    # 第一次96 DPI建立Canonical。
    #
    # 第二次150 DPI必须在：
    #
    # PowerPoint renderer
    # issue创建
    # flattened rebuild
    # output生成
    #
    # 之前直接拒绝。
    # ========================================================

    def test_dpi_change_is_rejected_before_second_issue_is_created(
        self,
    ):

        token_1 = (
            "3333333333333333"
        )

        token_2 = (
            "4444444444444444"
        )

        output_1 = (
            self.root
            / "dpi_96.pptx"
        )

        output_2 = (
            self.root
            / "dpi_150.pptx"
        )

        # ----------------------------------------------------
        # 第一次96 DPI正常发行。
        # ----------------------------------------------------

        with patch(
            "pptx_pipeline.render_pptx_pages",
            side_effect=
                self._fake_renderer,
        ):

            with patch(
                "pptx_pipeline."
                "rebuild_pptx_from_images",

                side_effect=
                    self._fake_rebuild,
            ):

                self._issue(
                    output_pptx=
                        output_1,

                    token=
                        token_1,

                    watermark_number=
                        "PPTX_DPI_LOCK_001",

                    dpi=
                        96,
                )

        registry_before = (
            load_document_registry(
                self.registry_path
            )
        )

        self.assertIn(
            token_1,
            registry_before[
                "issues"
            ],
        )

        self.assertNotIn(
            token_2,
            registry_before[
                "issues"
            ],
        )

        issue_count_before = len(
            registry_before[
                "issues"
            ]
        )

        # ----------------------------------------------------
        # 第二次要求150 DPI。
        #
        # Canonical验证应该首先拒绝。
        # ----------------------------------------------------

        with patch(
            "pptx_pipeline.render_pptx_pages",

            side_effect=
                AssertionError(
                    "DPI不匹配时不应该启动PowerPoint渲染"
                ),
        ) as renderer_mock:

            with patch(
                "pptx_pipeline."
                "rebuild_pptx_from_images",

                side_effect=
                    AssertionError(
                        "DPI不匹配时不应该重建PPTX"
                    ),
            ) as rebuild_mock:

                with self.assertRaisesRegex(
                    ValueError,
                    "Canonical DPI",
                ):

                    self._issue(
                        output_pptx=
                            output_2,

                        token=
                            token_2,

                        watermark_number=
                            "PPTX_DPI_LOCK_002",

                        dpi=
                            150,
                    )

        self.assertEqual(
            0,
            renderer_mock.call_count,
        )

        self.assertEqual(
            0,
            rebuild_mock.call_count,
        )

        # ----------------------------------------------------
        # 第二个Issue不能出现。
        # ----------------------------------------------------

        registry_after = (
            load_document_registry(
                self.registry_path
            )
        )

        self.assertEqual(
            issue_count_before,
            len(
                registry_after[
                    "issues"
                ]
            ),
        )

        self.assertIn(
            token_1,
            registry_after[
                "issues"
            ],
        )

        self.assertNotIn(
            token_2,
            registry_after[
                "issues"
            ],
        )

        # ----------------------------------------------------
        # 第二个输出也不能出现。
        # ----------------------------------------------------

        self.assertFalse(
            output_2.exists()
        )

        self.assertFalse(
            output_2.with_suffix(
                ".manifest.json"
            ).exists()
        )

        # ----------------------------------------------------
        # Document的Canonical DPI仍然保持第一次的96。
        # ----------------------------------------------------

        document = (
            registry_after[
                "documents"
            ][
                self.document_id
            ]
        )

        self.assertEqual(
            96,
            int(
                document[
                    "dpi"
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()