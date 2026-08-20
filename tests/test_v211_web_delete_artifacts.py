import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from web_demo.server import (
    DemoConfig,
    make_request_handler,
)


class WebDeleteArtifactTests(unittest.TestCase):

    def setUp(self):

        self.temp_dir = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temp_dir.name
        )

        self.config = DemoConfig(
            registry_path=(
                self.root
                / "document_registry.json"
            ),

            upload_dir=(
                self.root
                / "uploads"
            ),

            report_dir=(
                self.root
                / "reports"
            ),

            document_input_dir=(
                self.root
                / "document_inputs"
            ),

            document_output_dir=(
                self.root
                / "document_outputs"
            ),

            document_assets_dir=(
                self.root
                / "document_assets"
            ),

            trash_dir=(
                self.root
                / "trash"
            ),

            key="TEST_WEB_DELETE_KEY",

            debug=True,
        )

        self.Handler = (
            make_request_handler(
                self.config
            )
        )

        self.token = (
            "0123456789abcdef"
        )

    def tearDown(self):

        self.temp_dir.cleanup()

    # --------------------------------------------------------
    # 构造最小Handler。
    #
    # 不启动真实HTTP Server。
    # --------------------------------------------------------

    def _make_handler(self):

        handler = self.Handler.__new__(
            self.Handler
        )

        handler.headers = {
            "X-Confirm-Delete":
                self.token,
        }

        responses = []

        def fake_send_json(
            status,
            payload,
            request_id=None,
        ):

            responses.append({
                "status":
                    status,

                "payload":
                    payload,

                "request_id":
                    request_id,
            })

        handler._send_json = (
            fake_send_json
        )

        return (
            handler,
            responses,
        )

    # --------------------------------------------------------
    # 创建issue所属的watermarked_pages目录。
    # --------------------------------------------------------

    def _make_issue_dir(
        self,
        assets_dir,
    ):

        issue_dir = (
            assets_dir
            / "issues"
            / self.token
        )

        issue_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        (
            issue_dir
            / "page_001.png"
        ).write_bytes(
            b"FAKE_WATERMARKED_PAGE"
        )

        return issue_dir

    # ========================================================
    # 1. V2.1 PPTX
    #
    # 新记录使用 output_path。
    # PPTX最终发行物必须被移动到：
    #
    #   trash/.../watermarked.pptx
    # ========================================================

    def test_pptx_output_path_is_moved_to_trash(
        self,
    ):

        output_path = (
            self.root
            / "issued.pptx"
        )

        output_path.write_bytes(
            b"FAKE_PPTX_ARTIFACT"
        )

        manifest_path = (
            self.root
            / "manifest.json"
        )

        manifest_path.write_text(
            "{}",
            encoding="utf-8",
        )

        assets_dir = (
            self.root
            / "assets_pptx"
        )

        issue_dir = (
            self._make_issue_dir(
                assets_dir
            )
        )

        registry = {
            "documents": {
                "document-pptx": {
                    "assets_dir":
                        str(
                            assets_dir
                        ),

                    "source_type":
                        "pptx",

                    "source_name":
                        "source.pptx",
                },
            },

            "issues": {
                self.token: {
                    "document_id":
                        "document-pptx",

                    "status":
                        "issued",

                    "watermark_number":
                        "1001",

                    # V2.1通用字段。
                    "output_path":
                        str(
                            output_path
                        ),

                    "output_type":
                        "pptx",

                    "manifest_path":
                        str(
                            manifest_path
                        ),
                },
            },
        }

        (
            handler,
            responses,
        ) = self._make_handler()

        retired_issue = {
            "watermark_number":
                "1001",

            "status":
                "deleted",
        }

        with patch(
            "web_demo.server."
            "load_document_registry",
            return_value=registry,
        ), patch(
            "web_demo.server."
            "retire_document_issue",
            return_value=retired_issue,
        ) as mocked_retire:

            handler._handle_delete_watermark(
                self.token
            )

        # ----------------------------------------------------
        # 原发行物已经被移走。
        # ----------------------------------------------------

        self.assertFalse(
            output_path.exists()
        )

        self.assertFalse(
            manifest_path.exists()
        )

        self.assertFalse(
            issue_dir.exists()
        )

        # ----------------------------------------------------
        # trash目录应只生成一个本次删除目录。
        # ----------------------------------------------------

        trash_entries = list(
            self.config
            .trash_dir
            .iterdir()
        )

        self.assertEqual(
            1,
            len(
                trash_entries
            ),
        )

        trash_path = trash_entries[0]

        self.assertTrue(
            trash_path.is_dir()
        )

        # ----------------------------------------------------
        # PPTX必须保持.pptx扩展名。
        # ----------------------------------------------------

        self.assertTrue(
            (
                trash_path
                / "watermarked.pptx"
            ).is_file()
        )

        self.assertTrue(
            (
                trash_path
                / "manifest.json"
            ).is_file()
        )

        self.assertTrue(
            (
                trash_path
                / "watermarked_pages"
                / "page_001.png"
            ).is_file()
        )

        # ----------------------------------------------------
        # Registry retire必须在文件移动后执行。
        # ----------------------------------------------------

        mocked_retire.assert_called_once()

        args = (
            mocked_retire
            .call_args
            .args
        )

        kwargs = (
            mocked_retire
            .call_args
            .kwargs
        )

        self.assertEqual(
            self.config.registry_path,
            args[0],
        )

        self.assertEqual(
            self.token,
            args[1],
        )

        self.assertEqual(
            trash_path,
            kwargs[
                "trash_path"
            ],
        )

        # ----------------------------------------------------
        # Web响应。
        # ----------------------------------------------------

        self.assertEqual(
            1,
            len(
                responses
            ),
        )

        response = responses[0]

        self.assertEqual(
            HTTPStatus.OK,
            response[
                "status"
            ],
        )

        payload = response[
            "payload"
        ]

        self.assertTrue(
            payload[
                "deleted"
            ]
        )

        self.assertEqual(
            "1001",
            payload[
                "watermark_number"
            ],
        )

        self.assertEqual(
            3,
            payload[
                "moved_artifacts"
            ],
        )

        self.assertTrue(
            payload[
                "recoverable"
            ]
        )

    # ========================================================
    # 2. Legacy PDF
    #
    # 老注册记录可能没有output_path，
    # 只有output_pdf。
    #
    # 必须继续兼容。
    # ========================================================

    def test_legacy_pdf_output_pdf_is_still_moved_to_trash(
        self,
    ):

        output_pdf = (
            self.root
            / "issued.pdf"
        )

        output_pdf.write_bytes(
            b"%PDF-1.4\n"
            b"% legacy artifact\n"
        )

        manifest_path = (
            self.root
            / "manifest_legacy.json"
        )

        manifest_path.write_text(
            "{}",
            encoding="utf-8",
        )

        assets_dir = (
            self.root
            / "assets_pdf"
        )

        issue_dir = (
            self._make_issue_dir(
                assets_dir
            )
        )

        registry = {
            "documents": {
                "document-pdf": {
                    "assets_dir":
                        str(
                            assets_dir
                        ),

                    "source_type":
                        "pdf",

                    "source_name":
                        "source.pdf",
                },
            },

            "issues": {
                self.token: {
                    "document_id":
                        "document-pdf",

                    "status":
                        "issued",

                    "watermark_number":
                        "1002",

                    # Legacy PDF字段。
                    "output_pdf":
                        str(
                            output_pdf
                        ),

                    "manifest_path":
                        str(
                            manifest_path
                        ),
                },
            },
        }

        (
            handler,
            responses,
        ) = self._make_handler()

        retired_issue = {
            "watermark_number":
                "1002",

            "status":
                "deleted",
        }

        with patch(
            "web_demo.server."
            "load_document_registry",
            return_value=registry,
        ), patch(
            "web_demo.server."
            "retire_document_issue",
            return_value=retired_issue,
        ) as mocked_retire:

            handler._handle_delete_watermark(
                self.token
            )

        # ----------------------------------------------------
        # 原文件全部被移走。
        # ----------------------------------------------------

        self.assertFalse(
            output_pdf.exists()
        )

        self.assertFalse(
            manifest_path.exists()
        )

        self.assertFalse(
            issue_dir.exists()
        )

        trash_entries = list(
            self.config
            .trash_dir
            .iterdir()
        )

        self.assertEqual(
            1,
            len(
                trash_entries
            ),
        )

        trash_path = trash_entries[0]

        # ----------------------------------------------------
        # 老PDF仍然必须正确进入回收目录。
        # ----------------------------------------------------

        self.assertTrue(
            (
                trash_path
                / "watermarked.pdf"
            ).is_file()
        )

        self.assertTrue(
            (
                trash_path
                / "manifest.json"
            ).is_file()
        )

        self.assertTrue(
            (
                trash_path
                / "watermarked_pages"
                / "page_001.png"
            ).is_file()
        )

        mocked_retire.assert_called_once_with(
            self.config.registry_path,
            self.token,
            trash_path=trash_path,
        )

        # ----------------------------------------------------
        # Web响应。
        # ----------------------------------------------------

        self.assertEqual(
            1,
            len(
                responses
            ),
        )

        response = responses[0]

        self.assertEqual(
            HTTPStatus.OK,
            response[
                "status"
            ],
        )

        payload = response[
            "payload"
        ]

        self.assertTrue(
            payload[
                "deleted"
            ]
        )

        self.assertEqual(
            "1002",
            payload[
                "watermark_number"
            ],
        )

        self.assertEqual(
            3,
            payload[
                "moved_artifacts"
            ],
        )


if __name__ == "__main__":
    unittest.main()