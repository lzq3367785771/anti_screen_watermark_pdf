import unittest

from web_demo.server import (
    build_parser,
)


class WebCLICompatibilityTests(
    unittest.TestCase
):

    def test_new_document_cli_options(
        self,
    ):

        parser = build_parser()

        args = parser.parse_args([
            "--document-input-dir",
            "NEW_INPUT",

            "--document-output-dir",
            "NEW_OUTPUT",

            "--max-document-mb",
            "123",
        ])

        self.assertEqual(
            "NEW_INPUT",
            args.document_input_dir,
        )

        self.assertEqual(
            "NEW_OUTPUT",
            args.document_output_dir,
        )

        self.assertEqual(
            123,
            args.max_document_mb,
        )

    def test_legacy_pdf_cli_options_remain_compatible(
        self,
    ):

        parser = build_parser()

        args = parser.parse_args([
            "--pdf-input-dir",
            "OLD_INPUT",

            "--pdf-output-dir",
            "OLD_OUTPUT",

            "--max-pdf-mb",
            "88",
        ])

        # ----------------------------------------------------
        # 旧参数仍然接受，
        # 但最终必须落到新的通用dest。
        # ----------------------------------------------------

        self.assertEqual(
            "OLD_INPUT",
            args.document_input_dir,
        )

        self.assertEqual(
            "OLD_OUTPUT",
            args.document_output_dir,
        )

        self.assertEqual(
            88,
            args.max_document_mb,
        )

        # 不应再生成旧属性。
        self.assertFalse(
            hasattr(
                args,
                "pdf_input_dir",
            )
        )

        self.assertFalse(
            hasattr(
                args,
                "pdf_output_dir",
            )
        )

        self.assertFalse(
            hasattr(
                args,
                "max_pdf_mb",
            )
        )

    def test_legacy_pdf_cli_options_are_hidden_from_help(
        self,
    ):

        parser = build_parser()

        help_text = (
            parser.format_help()
        )

        # 新参数必须公开。
        self.assertIn(
            "--document-input-dir",
            help_text,
        )

        self.assertIn(
            "--document-output-dir",
            help_text,
        )

        self.assertIn(
            "--max-document-mb",
            help_text,
        )

        # ----------------------------------------------------
        # 旧参数只做兼容alias，
        # 不继续暴露给新用户。
        # ----------------------------------------------------

        self.assertNotIn(
            "--pdf-input-dir",
            help_text,
        )

        self.assertNotIn(
            "--pdf-output-dir",
            help_text,
        )

        self.assertNotIn(
            "--max-pdf-mb",
            help_text,
        )


if __name__ == "__main__":
    unittest.main()