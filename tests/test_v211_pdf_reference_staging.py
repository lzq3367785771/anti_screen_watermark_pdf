import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from document_registry import (
    register_document,
    sha256_file,
)
from pdf_pipeline import (
    _prepare_pdf_reference_set,
)


class PDFReferenceStagingTests(
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

        self.input_pdf.write_bytes(
            b"PDF_REFERENCE_STAGING_TEST"
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
                self.input_pdf
            )[:24]
        )

        self.document_assets = (
            self.assets_root
            / self.document_id
        )

        self.render_page_1 = (
            self.root
            / "render_001.png"
        )

        self.render_page_2 = (
            self.root
            / "render_002.png"
        )

        image_1 = np.full(
            (
                256,
                320,
                3,
            ),
            100,
            dtype=np.uint8,
        )

        image_2 = np.full(
            (
                256,
                320,
                3,
            ),
            160,
            dtype=np.uint8,
        )

        self.assertTrue(
            cv2.imwrite(
                str(
                    self.render_page_1
                ),
                image_1,
            )
        )

        self.assertTrue(
            cv2.imwrite(
                str(
                    self.render_page_2
                ),
                image_2,
            )
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _staging_dirs(self):

        if not self.document_assets.exists():
            return []

        return list(
            self.document_assets.glob(
                ".reference_pages_staging_*"
            )
        )

    # ----------------------------------------------------
    # 1. 已登记Canonical Reference时，
    #    必须直接复用。
    #
    #    render_pdf_pages()绝不能再次执行。
    # ----------------------------------------------------

    def test_registered_reference_is_reused_without_rendering(
        self,
    ):

        reference_dir = (
            self.document_assets
            / "reference_pages"
        )

        reference_dir.mkdir(
            parents=True,
        )

        reference_page = (
            reference_dir
            / "page_001.png"
        )

        reference_page.write_bytes(
            b"REGISTERED_CANONICAL_PAGE"
        )

        page_record = {
            "page_index":
                1,

            "width":
                320,

            "height":
                256,

            "reference_path":
                str(
                    reference_page.resolve()
                ),

            "reference_sha256":
                sha256_file(
                    reference_page
                ),
        }

        register_document(
            registry_path=
                self.registry_path,

            source_path=
                self.input_pdf,

            page_records=[
                page_record
            ],

            dpi=
                96,

            media_box_points=[
                240.0,
                192.0,
            ],

            assets_dir=
                self.document_assets,

            source_name=
                "source.pdf",

            source_type=
                "pdf",

            render_unit_type=
                "page",
        )

        with patch(
            "pdf_pipeline.render_pdf_pages",
            side_effect=
                AssertionError(
                    "不应该重新渲染"
                ),
        ):

            result = (
                _prepare_pdf_reference_set(
                    input_pdf=
                        self.input_pdf,

                    registry_path=
                        self.registry_path,

                    document_assets=
                        self.document_assets,

                    document_id=
                        self.document_id,

                    dpi=
                        96,
                )
            )

        self.assertTrue(
            result[
                "reused"
            ]
        )

        self.assertEqual(
            str(
                reference_page.resolve()
            ),
            result[
                "page_records"
            ][0][
                "reference_path"
            ],
        )

        self.assertEqual(
            [],
            self._staging_dirs(),
        )

    # ----------------------------------------------------
    # 2. 新Document：
    #
    #    所有页面先进入staging，
    #    完成后整个目录发布为reference_pages。
    # ----------------------------------------------------

    def test_new_reference_set_is_published_as_complete_directory(
        self,
    ):

        with patch(
            "pdf_pipeline.render_pdf_pages",
            return_value=(
                [
                    self.render_page_1,
                    self.render_page_2,
                ],
                "test warning",
            ),
        ):

            result = (
                _prepare_pdf_reference_set(
                    input_pdf=
                        self.input_pdf,

                    registry_path=
                        self.registry_path,

                    document_assets=
                        self.document_assets,

                    document_id=
                        self.document_id,

                    dpi=
                        96,
                )
            )

        self.assertFalse(
            result[
                "reused"
            ]
        )

        reference_dir = (
            self.document_assets
            / "reference_pages"
        )

        self.assertTrue(
            reference_dir.is_dir()
        )

        self.assertTrue(
            (
                reference_dir
                / "page_001.png"
            ).is_file()
        )

        self.assertTrue(
            (
                reference_dir
                / "page_002.png"
            ).is_file()
        )

        self.assertEqual(
            2,
            len(
                result[
                    "page_records"
                ]
            ),
        )

        for page_record in result[
            "page_records"
        ]:

            path = Path(
                page_record[
                    "reference_path"
                ]
            )

            self.assertTrue(
                path.is_file()
            )

            self.assertEqual(
                sha256_file(
                    path
                ),
                page_record[
                    "reference_sha256"
                ],
            )

        self.assertEqual(
            "test warning",
            result[
                "render_warnings"
            ],
        )

        self.assertEqual(
            [],
            self._staging_dirs(),
        )

    # ----------------------------------------------------
    # 3. staging页面写入中途失败：
    #
    #    final reference_pages绝不能出现。
    #    staging必须清理。
    # ----------------------------------------------------

    def test_partial_staging_failure_never_publishes_reference_directory(
        self,
    ):

        original_imwrite = (
            cv2.imwrite
        )

        call_count = {
            "value": 0
        }

        def failing_imwrite(
            path,
            image,
        ):

            call_count[
                "value"
            ] += 1

            if (
                call_count[
                    "value"
                ]
                == 2
            ):
                return False

            return original_imwrite(
                path,
                image,
            )

        with patch(
            "pdf_pipeline.render_pdf_pages",
            return_value=(
                [
                    self.render_page_1,
                    self.render_page_2,
                ],
                "",
            ),
        ):

            with patch(
                "pdf_pipeline.cv2.imwrite",
                side_effect=
                    failing_imwrite,
            ):

                with self.assertRaisesRegex(
                    RuntimeError,
                    "staging参考页面",
                ):

                    _prepare_pdf_reference_set(
                        input_pdf=
                            self.input_pdf,

                        registry_path=
                            self.registry_path,

                        document_assets=
                            self.document_assets,

                        document_id=
                            self.document_id,

                        dpi=
                            96,
                    )

        reference_dir = (
            self.document_assets
            / "reference_pages"
        )

        self.assertFalse(
            reference_dir.exists()
        )

        self.assertEqual(
            [],
            self._staging_dirs(),
        )

    # ----------------------------------------------------
    # 4. Registry没有登记，
    #    但正式reference_pages已经存在。
    #
    #    不允许覆盖，也不允许删除。
    # ----------------------------------------------------

    def test_unregistered_existing_reference_directory_is_preserved(
        self,
    ):

        reference_dir = (
            self.document_assets
            / "reference_pages"
        )

        reference_dir.mkdir(
            parents=True,
        )

        marker = (
            reference_dir
            / "do_not_touch.txt"
        )

        marker.write_text(
            "OLD_UNKNOWN_REFERENCE",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            FileExistsError,
            "Registry",
        ):

            _prepare_pdf_reference_set(
                input_pdf=
                    self.input_pdf,

                registry_path=
                    self.registry_path,

                document_assets=
                    self.document_assets,

                document_id=
                    self.document_id,

                dpi=
                    96,
            )

        self.assertTrue(
            marker.is_file()
        )

        self.assertEqual(
            "OLD_UNKNOWN_REFERENCE",
            marker.read_text(
                encoding="utf-8",
            ),
        )

        self.assertEqual(
            [],
            self._staging_dirs(),
        )


if __name__ == "__main__":
    unittest.main()