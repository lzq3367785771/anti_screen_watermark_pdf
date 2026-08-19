import tempfile
import unittest
from pathlib import Path

from office_renderer import (
    _points_to_pixels,
    _slide_pixel_size,
    render_pptx_pages,
)


class PowerPointRendererTests(
    unittest.TestCase
):

    def test_points_to_pixels(self):

        self.assertEqual(
            _points_to_pixels(
                72,
                96,
            ),
            96,
        )

        self.assertEqual(
            _points_to_pixels(
                720,
                96,
            ),
            960,
        )


    def test_slide_pixel_size(self):

        width, height = (
            _slide_pixel_size(
                960,
                540,
                96,
            )
        )

        self.assertEqual(
            width,
            1280,
        )

        self.assertEqual(
            height,
            720,
        )


    def test_invalid_dpi_is_rejected(self):

        with self.assertRaises(
            ValueError
        ):

            _points_to_pixels(
                720,
                0,
            )


    def test_missing_pptx_is_rejected(self):

        with tempfile.TemporaryDirectory() as temporary:

            root = Path(
                temporary
            )

            source = (
                root / "missing.pptx"
            )

            with self.assertRaises(
                FileNotFoundError
            ):

                render_pptx_pages(
                    source,
                    root / "output",
                )


    def test_wrong_extension_is_rejected(self):

        with tempfile.TemporaryDirectory() as temporary:

            root = Path(
                temporary
            )

            source = (
                root / "fake.pdf"
            )

            source.write_bytes(
                b"dummy"
            )

            with self.assertRaisesRegex(
                ValueError,
                ".pptx",
            ):

                render_pptx_pages(
                    source,
                    root / "output",
                )


if __name__ == "__main__":
    unittest.main()