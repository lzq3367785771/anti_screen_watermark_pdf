import tempfile
import unittest
from pathlib import Path

from document_registry import (
    load_registered_reference_set,
    register_document,
    sha256_file,
)


class ReferenceSetTests(unittest.TestCase):

    def setUp(self):

        self.temp_dir = (
            tempfile.TemporaryDirectory()
        )

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

        self.source_path.write_bytes(
            b"REFERENCE_SET_TEST_SOURCE"
        )

        self.reference_dir = (
            self.root
            / "reference_pages"
        )

        self.reference_dir.mkdir()

        self.reference_page = (
            self.reference_dir
            / "page_001.png"
        )

        self.reference_page.write_bytes(
            b"CANONICAL_REFERENCE_PAGE"
        )

        self.page_record = {
            "page_index": 1,
            "unit_type": "page",
            "width": 100,
            "height": 120,
            "reference_path":
                str(
                    self.reference_page.resolve()
                ),
            "reference_sha256":
                sha256_file(
                    self.reference_page
                ),
        }

        (
            self.document_id,
            self.document,
        ) = register_document(
            registry_path=
                self.registry_path,

            source_path=
                self.source_path,

            page_records=[
                self.page_record
            ],

            dpi=96,

            media_box_points=[
                75.0,
                90.0,
            ],

            assets_dir=
                self.root,

            source_name=
                "source.pdf",

            source_type=
                "pdf",

            render_unit_type=
                "page",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_document_returns_none(
        self,
    ):
        result = (
            load_registered_reference_set(
                self.registry_path,
                "ffffffffffffffffffffffff",
                expected_source_type=
                    "pdf",
                expected_dpi=
                    96,
            )
        )

        self.assertIsNone(
            result
        )

    def test_valid_reference_set_is_reusable(
        self,
    ):
        result = (
            load_registered_reference_set(
                self.registry_path,
                self.document_id,
                expected_source_type=
                    "pdf",
                expected_dpi=
                    96,
            )
        )

        self.assertIsNotNone(
            result
        )

        self.assertEqual(
            self.document_id,
            result[
                "document_id"
            ],
        )

        self.assertEqual(
            1,
            len(
                result[
                    "pages"
                ]
            ),
        )

        self.assertEqual(
            str(
                self.reference_page.resolve()
            ),
            result[
                "pages"
            ][0][
                "reference_path"
            ],
        )

    def test_dpi_mismatch_is_rejected(
        self,
    ):
        with self.assertRaisesRegex(
            ValueError,
            "Canonical DPI",
        ):
            load_registered_reference_set(
                self.registry_path,
                self.document_id,
                expected_source_type=
                    "pdf",
                expected_dpi=
                    150,
            )

    def test_missing_reference_file_is_rejected(
        self,
    ):
        self.reference_page.unlink()

        with self.assertRaises(
            FileNotFoundError
        ):
            load_registered_reference_set(
                self.registry_path,
                self.document_id,
                expected_source_type=
                    "pdf",
                expected_dpi=
                    96,
            )

    def test_reference_hash_mismatch_is_rejected(
        self,
    ):
        self.reference_page.write_bytes(
            b"CORRUPTED_REFERENCE_PAGE"
        )

        with self.assertRaisesRegex(
            ValueError,
            "SHA256",
        ):
            load_registered_reference_set(
                self.registry_path,
                self.document_id,
                expected_source_type=
                    "pdf",
                expected_dpi=
                    96,
            )


if __name__ == "__main__":
    unittest.main()