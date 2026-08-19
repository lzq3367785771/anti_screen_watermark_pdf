import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from document_pipeline import (
    DOCUMENT_PIPELINE_VERSION,
    detect_document_type,
    embed_document,
)


class DocumentPipelineTests(unittest.TestCase):

    def test_pipeline_version(self):

        self.assertEqual(
            DOCUMENT_PIPELINE_VERSION,
            "v2.1.0",
        )


    def test_detect_supported_document_types(self):

        self.assertEqual(
            detect_document_type("example.pdf"),
            "pdf",
        )

        self.assertEqual(
            detect_document_type("example.docx"),
            "docx",
        )

        self.assertEqual(
            detect_document_type("example.pptx"),
            "pptx",
        )

        self.assertEqual(
            detect_document_type("example.xlsx"),
            "xlsx",
        )


    def test_unknown_document_type_is_rejected(self):

        with self.assertRaisesRegex(
            ValueError,
            "不支持的文档类型",
        ):

            detect_document_type(
                "example.txt"
            )


    def test_office_document_is_recognized_but_not_implemented(self):

        with tempfile.TemporaryDirectory() as temporary:

            root = Path(temporary)

            source = root / "meeting.pptx"

            source.write_bytes(
                b"dummy-pptx-v210-test"
            )

            registry_path = (
                root / "registry.json"
            )

            with self.assertRaisesRegex(
                NotImplementedError,
                "PPTX",
            ):

                embed_document(
                    source,
                    registry_path,
                )


    def test_pdf_is_dispatched_to_existing_pdf_pipeline(self):

        with tempfile.TemporaryDirectory() as temporary:

            root = Path(temporary)

            source = root / "source.pdf"

            source.write_bytes(
                b"%PDF-1.4\n"
            )

            registry_path = (
                root / "registry.json"
            )

            output_path = (
                root / "output.pdf"
            )

            expected_result = (
                root / "output.manifest.json",
                {
                    "document_id": "test-document",
                    "trace_token": "0123456789abcdef",
                },
            )

            with patch(
                "document_pipeline.embed_document_pdf"
            ) as mocked_embed_pdf:

                mocked_embed_pdf.return_value = (
                    expected_result
                )

                actual_result = embed_document(
                    source,
                    registry_path,
                    key="test-key",
                    output_path=output_path,
                    dpi=96,
                    alpha=72.0,
                    repeat=24,
                    pilot_bits=64,
                    pilot_repeat=8,
                    pilot_alpha=90.0,
                    recipient="test-user",
                    session="v210-test",
                    watermark_number="WM-V210-001",
                )

            self.assertEqual(
                actual_result,
                expected_result,
            )

            mocked_embed_pdf.assert_called_once()

            call = (
                mocked_embed_pdf.call_args
            )

            self.assertEqual(
                call.kwargs["input_pdf"],
                source.resolve(),
            )

            self.assertEqual(
                Path(
                    call.kwargs["registry_path"]
                ),
                registry_path,
            )

            self.assertEqual(
                call.kwargs["output_pdf"],
                output_path,
            )

            self.assertEqual(
                call.kwargs["dpi"],
                96,
            )

            self.assertEqual(
                call.kwargs["alpha"],
                72.0,
            )

            self.assertEqual(
                call.kwargs["repeat"],
                24,
            )

            self.assertEqual(
                call.kwargs["pilot_repeat"],
                8,
            )

            self.assertEqual(
                call.kwargs["pilot_alpha"],
                90.0,
            )

            self.assertEqual(
                call.kwargs["watermark_number"],
                "WM-V210-001",
            )


if __name__ == "__main__":
    unittest.main()