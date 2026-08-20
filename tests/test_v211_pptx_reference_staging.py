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
from pptx_pipeline import (
    _prepare_pptx_reference_set,
)


class PPTXReferenceStagingTests(
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

        self.input_pptx.write_bytes(
            b"PPTX_REFERENCE_STAGING_TEST"
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

        self.document_assets = (
            self.assets_root
            / self.document_id
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

    def _write_slide(
        self,
        path,
        value,
    ):

        image = np.full(
            (
                240,
                320,
                3,
            ),
            value,
            dtype=np.uint8,
        )

        self.assertTrue(
            cv2.imwrite(
                str(path),
                image,
            )
        )

    # ----------------------------------------------------
    # 1. 已登记Canonical：
    #    不允许重新启动PowerPoint renderer。
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
            / "slide_001.png"
        )

        self._write_slide(
            reference_page,
            100,
        )

        page_record = {
            "page_index":
                1,

            "unit_type":
                "slide",

            "slide_index":
                1,

            "width":
                320,

            "height":
                240,

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
                self.input_pptx,

            page_records=[
                page_record
            ],

            dpi=
                96,

            media_box_points=[
                720.0,
                540.0,
            ],

            assets_dir=
                self.document_assets,

            source_name=
                "source.pptx",

            source_type=
                "pptx",

            render_unit_type=
                "slide",
        )

        with patch(
            "pptx_pipeline.render_pptx_pages",
            side_effect=
                AssertionError(
                    "不应该重新启动PowerPoint渲染"
                ),
        ):

            result = (
                _prepare_pptx_reference_set(
                    input_pptx=
                        self.input_pptx,

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
            720.0,
            result[
                "render_info"
            ][
                "slide_width_points"
            ],
        )

        self.assertEqual(
            [],
            self._staging_dirs(),
        )

    # ----------------------------------------------------
    # 2. 新PPTX：
    #    renderer只向staging写文件，
    #    完整后整个目录发布。
    # ----------------------------------------------------

    def test_new_reference_set_is_published_as_complete_directory(
        self,
    ):

        def fake_renderer(
            pptx_path,
            output_dir,
            dpi=96,
        ):

            output_dir = Path(
                output_dir
            )

            slide_1 = (
                output_dir
                / "slide_001.png"
            )

            slide_2 = (
                output_dir
                / "slide_002.png"
            )

            self._write_slide(
                slide_1,
                100,
            )

            self._write_slide(
                slide_2,
                160,
            )

            return (
                [
                    slide_1,
                    slide_2,
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
                        2,

                    "slide_width_points":
                        720.0,

                    "slide_height_points":
                        540.0,

                    "width":
                        320,

                    "height":
                        240,
                },
            )

        with patch(
            "pptx_pipeline.render_pptx_pages",
            side_effect=
                fake_renderer,
        ):

            result = (
                _prepare_pptx_reference_set(
                    input_pptx=
                        self.input_pptx,

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
                / "slide_001.png"
            ).is_file()
        )

        self.assertTrue(
            (
                reference_dir
                / "slide_002.png"
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

            reference_path = Path(
                page_record[
                    "reference_path"
                ]
            )

            self.assertTrue(
                reference_path.is_file()
            )

            self.assertEqual(
                sha256_file(
                    reference_path
                ),
                page_record[
                    "reference_sha256"
                ],
            )

        self.assertEqual(
            720.0,
            result[
                "media_box_points"
            ][0],
        )

        self.assertEqual(
            [],
            self._staging_dirs(),
        )

    # ----------------------------------------------------
    # 3. PowerPoint导出到一半失败：
    #
    #    partial slide只能存在于staging，
    #    final reference_pages绝不能出现。
    # ----------------------------------------------------

    def test_partial_render_failure_never_publishes_reference_directory(
        self,
    ):

        def failing_renderer(
            pptx_path,
            output_dir,
            dpi=96,
        ):

            output_dir = Path(
                output_dir
            )

            partial = (
                output_dir
                / "slide_001.png"
            )

            self._write_slide(
                partial,
                100,
            )

            raise RuntimeError(
                "forced PowerPoint export failure"
            )

        with patch(
            "pptx_pipeline.render_pptx_pages",
            side_effect=
                failing_renderer,
        ):

            with self.assertRaisesRegex(
                RuntimeError,
                "forced PowerPoint export failure",
            ):

                _prepare_pptx_reference_set(
                    input_pptx=
                        self.input_pptx,

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
    #    但正式reference_pages已经存在：
    #
    #    必须原样保留。
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
            "UNKNOWN_OLD_REFERENCE",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            FileExistsError,
            "Registry",
        ):

            _prepare_pptx_reference_set(
                input_pptx=
                    self.input_pptx,

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
            "UNKNOWN_OLD_REFERENCE",
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