import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from document_pipeline import (
    embed_document,
)

from pptx_rebuilder import (
    rebuild_pptx_from_images,
)


class PPTXPipelineTests(
    unittest.TestCase
):

    def test_rebuilder_rejects_empty_images(self):

        with tempfile.TemporaryDirectory() as temporary:

            root = Path(
                temporary
            )

            with self.assertRaisesRegex(
                ValueError,
                "没有可用于重建PPTX",
            ):

                rebuild_pptx_from_images(
                    [],
                    root / "output.pptx",
                    960,
                    540,
                )


    def test_rebuilder_rejects_wrong_output_extension(self):

        with tempfile.TemporaryDirectory() as temporary:

            root = Path(
                temporary
            )

            image_path = (
                root / "slide.png"
            )

            image_path.write_bytes(
                b"dummy"
            )

            with self.assertRaisesRegex(
                ValueError,
                ".pptx",
            ):

                rebuild_pptx_from_images(
                    [image_path],
                    root / "output.pdf",
                    960,
                    540,
                )


    def test_document_pipeline_dispatches_pptx(self):

        with tempfile.TemporaryDirectory() as temporary:

            root = Path(
                temporary
            )

            source = (
                root / "meeting.pptx"
            )

            source.write_bytes(
                b"dummy-pptx"
            )

            registry = (
                root / "registry.json"
            )

            expected = (
                root / "manifest.json",
                {
                    "source_type":
                        "pptx",
                },
            )

            with patch(
                "document_pipeline."
                "embed_document_pptx"
            ) as mocked_embed:

                mocked_embed.return_value = (
                    expected
                )

                result = embed_document(
                    source,
                    registry,

                    dpi=96,

                    alpha=72.0,

                    repeat=24,

                    pilot_bits=64,

                    pilot_repeat=8,

                    pilot_alpha=90.0,

                    watermark_number=
                        "PPT-001",
                )

            self.assertEqual(
                result,
                expected,
            )

            mocked_embed.assert_called_once()

            kwargs = (
                mocked_embed
                .call_args
                .kwargs
            )

            self.assertEqual(
                kwargs["dpi"],
                96,
            )

            self.assertEqual(
                kwargs["alpha"],
                72.0,
            )

            self.assertEqual(
                kwargs["repeat"],
                24,
            )

            self.assertEqual(
                kwargs[
                    "watermark_number"
                ],
                "PPT-001",
            )


if __name__ == "__main__":
    unittest.main()