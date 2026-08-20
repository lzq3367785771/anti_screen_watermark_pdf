import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from web_demo.server import (
    DemoConfig,
    make_request_handler,
)


class WebEmbedProfileTests(unittest.TestCase):

    def setUp(self):

        self.temp_dir = tempfile.TemporaryDirectory()

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

            pdf_input_dir=(
                self.root
                / "document_inputs"
            ),

            pdf_output_dir=(
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

            key="TEST_WEB_PROFILE_KEY",

            debug=True,
        )

        self.Handler = make_request_handler(
            self.config
        )

    def tearDown(self):

        self.temp_dir.cleanup()

    # --------------------------------------------------------
    # 构造最小化Handler
    #
    # 不调用BaseHTTPRequestHandler.__init__，
    # 因为那会要求真实socket。
    #
    # _handle_embed()实际只需要：
    #
    #   self.headers
    #   self._receive_pdf()
    #   self._send_json()
    # --------------------------------------------------------

    def _make_handler(
        self,
        source_path,
    ):

        handler = self.Handler.__new__(
            self.Handler
        )

        handler.headers = {
            "X-Watermark-Number":
                "1001",
        }

        byte_count = len(
            source_path.read_bytes()
        )

        def fake_receive(
            request_id,
        ):

            return (
                source_path,
                source_path.name,
                byte_count,
            )

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

        handler._receive_pdf = (
            fake_receive
        )

        handler._send_json = (
            fake_send_json
        )

        return (
            handler,
            responses,
        )

    # ========================================================
    # PDF
    #
    # Web收到PDF以后，
    # 必须把PDF专属Profile传给embed_document()。
    # ========================================================

    def test_pdf_upload_uses_pdf_embed_profile(
        self,
    ):

        source = (
            self.root
            / "source.pdf"
        )

        source.write_bytes(
            b"%PDF-1.4\n"
            b"% web profile test\n"
        )

        (
            handler,
            responses,
        ) = self._make_handler(
            source
        )

        manifest_path = (
            self.root
            / "manifest_pdf.json"
        )

        manifest = {
            "trace_token":
                "1111111111111111",

            "trace_id":
                "a" * 32,

            "document_id":
                "b" * 24,

            "page_count":
                1,

            "render_unit_type":
                "page",

            "dpi":
                150,

            "watermark": {
                "block_size":
                    8,

                "repeat":
                    16,

                "alpha":
                    42.0,
            },
        }

        with patch(
            "web_demo.server."
            "embed_document"
        ) as mocked_embed:

            mocked_embed.return_value = (
                manifest_path,
                manifest,
            )

            handler._handle_embed(
                "request-pdf-001"
            )

        # ----------------------------------------------------
        # 必须真正调用统一Document Pipeline一次。
        # ----------------------------------------------------

        mocked_embed.assert_called_once()

        kwargs = (
            mocked_embed
            .call_args
            .kwargs
        )

        # ----------------------------------------------------
        # 输入与类型
        # ----------------------------------------------------

        self.assertEqual(
            source.resolve(),
            Path(
                kwargs[
                    "input_path"
                ]
            ).resolve(),
        )

        self.assertEqual(
            "pdf",
            kwargs[
                "source_type"
            ],
        )

        self.assertEqual(
            ".pdf",
            Path(
                kwargs[
                    "output_path"
                ]
            ).suffix.lower(),
        )

        # ----------------------------------------------------
        # PDF Profile
        # ----------------------------------------------------

        self.assertEqual(
            150,
            kwargs[
                "dpi"
            ],
        )

        self.assertEqual(
            42.0,
            kwargs[
                "alpha"
            ],
        )

        self.assertEqual(
            16,
            kwargs[
                "repeat"
            ],
        )

        self.assertEqual(
            64,
            kwargs[
                "pilot_bits"
            ],
        )

        self.assertEqual(
            6,
            kwargs[
                "pilot_repeat"
            ],
        )

        self.assertEqual(
            78.0,
            kwargs[
                "pilot_alpha"
            ],
        )

        self.assertEqual(
            "1001",
            kwargs[
                "watermark_number"
            ],
        )

        # ----------------------------------------------------
        # Web响应
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
                "success"
            ]
        )

        self.assertEqual(
            "pdf",
            payload[
                "source_type"
            ],
        )

        self.assertEqual(
            "PDF",
            payload[
                "document_type"
            ],
        )

        # PDF支持在线预览。
        self.assertIsNotNone(
            payload[
                "preview_url"
            ]
        )

        self.assertEqual(
            150,
            payload[
                "technical"
            ][
                "dpi"
            ],
        )

        self.assertEqual(
            16,
            payload[
                "technical"
            ][
                "payload_repeat"
            ],
        )

        self.assertEqual(
            42.0,
            payload[
                "technical"
            ][
                "payload_alpha"
            ],
        )

    # ========================================================
    # PPTX
    #
    # Web收到PPTX以后，
    # 必须使用PPTX专属Profile。
    # ========================================================

    def test_pptx_upload_uses_pptx_embed_profile(
        self,
    ):

        source = (
            self.root
            / "source.pptx"
        )

        source.write_bytes(
            b"FAKE_PPTX_FOR_WEB_PROFILE_TEST"
        )

        (
            handler,
            responses,
        ) = self._make_handler(
            source
        )

        manifest_path = (
            self.root
            / "manifest_pptx.json"
        )

        manifest = {
            "trace_token":
                "2222222222222222",

            "trace_id":
                "c" * 32,

            "document_id":
                "d" * 24,

            "page_count":
                1,

            "render_unit_type":
                "slide",

            "dpi":
                96,

            "watermark": {
                "block_size":
                    8,

                "repeat":
                    24,

                "alpha":
                    72.0,
            },
        }

        with patch(
            "web_demo.server."
            "embed_document"
        ) as mocked_embed:

            mocked_embed.return_value = (
                manifest_path,
                manifest,
            )

            handler._handle_embed(
                "request-pptx-001"
            )

        mocked_embed.assert_called_once()

        kwargs = (
            mocked_embed
            .call_args
            .kwargs
        )

        # ----------------------------------------------------
        # 输入与类型
        # ----------------------------------------------------

        self.assertEqual(
            source.resolve(),
            Path(
                kwargs[
                    "input_path"
                ]
            ).resolve(),
        )

        self.assertEqual(
            "pptx",
            kwargs[
                "source_type"
            ],
        )

        self.assertEqual(
            ".pptx",
            Path(
                kwargs[
                    "output_path"
                ]
            ).suffix.lower(),
        )

        # ----------------------------------------------------
        # PPTX Profile
        # ----------------------------------------------------

        self.assertEqual(
            96,
            kwargs[
                "dpi"
            ],
        )

        self.assertEqual(
            72.0,
            kwargs[
                "alpha"
            ],
        )

        self.assertEqual(
            24,
            kwargs[
                "repeat"
            ],
        )

        self.assertEqual(
            64,
            kwargs[
                "pilot_bits"
            ],
        )

        self.assertEqual(
            8,
            kwargs[
                "pilot_repeat"
            ],
        )

        self.assertEqual(
            90.0,
            kwargs[
                "pilot_alpha"
            ],
        )

        self.assertEqual(
            "1001",
            kwargs[
                "watermark_number"
            ],
        )

        # ----------------------------------------------------
        # Web响应
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
                "success"
            ]
        )

        self.assertEqual(
            "pptx",
            payload[
                "source_type"
            ],
        )

        self.assertEqual(
            "PPTX",
            payload[
                "document_type"
            ],
        )

        # PPTX目前不做浏览器在线预览。
        self.assertIsNone(
            payload[
                "preview_url"
            ]
        )

        self.assertEqual(
            96,
            payload[
                "technical"
            ][
                "dpi"
            ],
        )

        self.assertEqual(
            24,
            payload[
                "technical"
            ][
                "payload_repeat"
            ],
        )

        self.assertEqual(
            72.0,
            payload[
                "technical"
            ][
                "payload_alpha"
            ],
        )


if __name__ == "__main__":
    unittest.main()