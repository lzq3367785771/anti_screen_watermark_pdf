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


class PDFReferenceIntegrationTests(
    unittest.TestCase
):

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

        # 测试中render_pdf_pages会被patch，
        # 所以源文件只需要稳定存在并能计算SHA256。
        self.input_pdf.write_bytes(
            b"%PDF-1.4\n"
            b"% V2.1.1 canonical integration test\n"
        )

        self.registry_path = (
            self.root
            / "document_registry.json"
        )

        self.assets_root = (
            self.root
            / "assets"
        )

        self.rendered_page = (
            self.root
            / "rendered_page.png"
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

        self.assertTrue(
            cv2.imwrite(
                str(
                    self.rendered_page
                ),
                image,
            )
        )

        self.document_id = (
            sha256_file(
                self.input_pdf
            )[:24]
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _issue(
        self,
        output_pdf,
        token,
        watermark_number,
        dpi=96,
    ):
        return embed_document_pdf(
            input_pdf=
                self.input_pdf,

            registry_path=
                self.registry_path,

            key=
                "TEST_CANONICAL_REFERENCE_KEY",

            output_pdf=
                output_pdf,

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
                "canonical_reference_test",

            notes=
                "V2.1.1-F4-A3-Test",

            trace_token=
                token,

            watermark_number=
                watermark_number,

            source_name=
                "source.pdf",
        )

    # ----------------------------------------------------
    # 1.
    # 第一次发行建立Canonical Reference。
    #
    # 第二次发行同一源PDF / 同一DPI时，
    # render_pdf_pages()绝不能再次执行。
    # ----------------------------------------------------

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
            / "watermarked_1.pdf"
        )

        output_2 = (
            self.root
            / "watermarked_2.pdf"
        )

        # ------------------------------------------------
        # 第一次发行：
        # 允许真正进入render_pdf_pages patch，
        # 从而建立Canonical Reference。
        # ------------------------------------------------

        with patch(
            "pdf_pipeline.render_pdf_pages",
            return_value=(
                [
                    self.rendered_page
                ],
                "first render",
            ),
        ) as first_render:

            (
                manifest_path_1,
                manifest_1,
            ) = self._issue(
                output_pdf=
                    output_1,

                token=
                    token_1,

                watermark_number=
                    "CANONICAL_001",

                dpi=
                    96,
            )

        self.assertEqual(
            1,
            first_render.call_count,
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

        # ------------------------------------------------
        # 记录第一次建立后的Canonical Reference。
        # ------------------------------------------------

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

        self.assertTrue(
            first_pages
        )

        first_reference_path = (
            Path(
                first_pages[0][
                    "reference_path"
                ]
            )
        )

        first_reference_sha256 = (
            first_pages[0][
                "reference_sha256"
            ]
        )

        self.assertTrue(
            first_reference_path.is_file()
        )

        # ------------------------------------------------
        # 第二次发行：
        #
        # 如果代码又调用render_pdf_pages()，
        # 测试必须立即失败。
        # ------------------------------------------------

        with patch(
            "pdf_pipeline.render_pdf_pages",
            side_effect=
                AssertionError(
                    "第二次发行不应该重新渲染PDF"
                ),
        ) as second_render:

            (
                manifest_path_2,
                manifest_2,
            ) = self._issue(
                output_pdf=
                    output_2,

                token=
                    token_2,

                watermark_number=
                    "CANONICAL_002",

                dpi=
                    96,
            )

        self.assertEqual(
            0,
            second_render.call_count,
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

        # ------------------------------------------------
        # 两次发行应该属于同一个Document，
        # 但拥有两个独立Issue。
        # ------------------------------------------------

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

    # ----------------------------------------------------
    # 2.
    # 同一个Document第一次以96 DPI建立Canonical。
    #
    # 第二次要求150 DPI时必须在：
    #
    #   render
    #   issue创建
    #
    # 之前直接拒绝。
    # ----------------------------------------------------

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
            / "dpi_96.pdf"
        )

        output_2 = (
            self.root
            / "dpi_150.pdf"
        )

        # ------------------------------------------------
        # 先用96 DPI正常建立Document + Canonical。
        # ------------------------------------------------

        with patch(
            "pdf_pipeline.render_pdf_pages",
            return_value=(
                [
                    self.rendered_page
                ],
                "",
            ),
        ):

            self._issue(
                output_pdf=
                    output_1,

                token=
                    token_1,

                watermark_number=
                    "DPI_LOCK_001",

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

        # ------------------------------------------------
        # 第二次改成150 DPI。
        #
        # Canonical检查应该先报错，
        # 所以render_pdf_pages()也绝不能执行。
        # ------------------------------------------------

        with patch(
            "pdf_pipeline.render_pdf_pages",
            side_effect=
                AssertionError(
                    "DPI不匹配时不应该重新渲染"
                ),
        ) as render_mock:

            with self.assertRaisesRegex(
                ValueError,
                "Canonical DPI",
            ):

                self._issue(
                    output_pdf=
                        output_2,

                    token=
                        token_2,

                    watermark_number=
                        "DPI_LOCK_002",

                    dpi=
                        150,
                )

        self.assertEqual(
            0,
            render_mock.call_count,
        )

        # ------------------------------------------------
        # 被拒绝的第二次发行不能创建issue。
        # ------------------------------------------------

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

        # 第二个输出也不应产生。
        self.assertFalse(
            output_2.exists()
        )

        # Canonical DPI仍然保持第一次的96。
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