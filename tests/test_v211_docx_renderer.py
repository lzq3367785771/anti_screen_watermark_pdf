from pathlib import Path
import tempfile
import unittest


from office_renderer import (
    check_word_backend,
    render_docx_pages,
)


class TestDOCXRenderer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            check_word_backend()
        except Exception as exc:
            raise unittest.SkipTest(
                f"Microsoft Word/pywin32 backend unavailable: {exc}"
            )

    def test_word_backend(self):

        result = check_word_backend()

        self.assertTrue(
            result["available"]
        )


    def test_docx_render_three_pages(self):

        # 这里先不自动生成docx
        # 使用固定测试文件

        docx_path = Path(
            "test_docx/simple.docx"
        ).resolve()

        self.assertTrue(
            docx_path.is_file()
        )


        with tempfile.TemporaryDirectory() as tmp:

            pages, info = render_docx_pages(
                docx_path,
                tmp,
                dpi=150,
            )


            self.assertEqual(
                info["source_type"],
                "docx",
            )

            self.assertEqual(
                info["render_unit_type"],
                "page",
            )

            self.assertEqual(
                info["page_count"],
                3,
            )


            self.assertEqual(
                len(pages),
                3,
            )


            for page in pages:

                self.assertTrue(
                    Path(page).is_file()
                )


    def test_docx_page_geometry(self):

        docx_path = Path(
            "test_docx/simple.docx"
        ).resolve()


        with tempfile.TemporaryDirectory() as tmp:

            _, info = render_docx_pages(
                docx_path,
                tmp,
                dpi=150,
            )


            for page in info["page_sizes"]:

                self.assertGreater(
                    page["width"],
                    0,
                )

                self.assertGreater(
                    page["height"],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
