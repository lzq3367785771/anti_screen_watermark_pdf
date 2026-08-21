import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from docx_pipeline import embed_docx
from document_registry import (
    DOCUMENT_CODEWORD_BITS,
    key_id_for_key,
    load_document_registry,
    sha256_file,
    validate_key_id,
)
from pdf_pipeline import (
    _traceable_registry_for_key,
    normalize_document_manifest,
)


class DOCXManifestV212Tests(unittest.TestCase):

    def test_key_id_is_derived_and_mismatch_is_rejected(self):
        key = "DOCX-TEST-KEY"
        self.assertEqual(validate_key_id(key), key_id_for_key(key))
        with self.assertRaisesRegex(ValueError, "key_id与水印密钥不匹配"):
            validate_key_id(key, "0000000000000000")

    def test_legacy_docx_manifest_recovers_actual_repeat_and_pilot(self):
        manifest = normalize_document_manifest({
            "schema_version": 1,
            "key_id": "0123456789abcdef",
            "page_count": 1,
            "watermark": {
                "block_size": 8,
                "bit_count": DOCUMENT_CODEWORD_BITS,
                "repeat": 6,
                "sync_pilot": {
                    "bits": 64,
                    "repeat": 6,
                    "alpha": 78.0,
                },
            },
            "pages": [{
                "page_index": 1,
                "payload_embedding": {"dct_units": 2240},
                "pilot_embedding": {"dct_units": 384},
            }],
        })
        watermark = manifest["watermark"]
        self.assertEqual(watermark["repeat"], 16)
        self.assertTrue(watermark["sync_pilot"]["enabled"])
        self.assertEqual(watermark["sync_pilot"]["bit_count"], 64)
        self.assertEqual(watermark["sync_pilot"]["position_offset"], 2240)

    def test_trace_candidates_exclude_unfinished_and_wrong_key_issues(self):
        registry = {
            "issues": {
                "complete": {
                    "status": "issued",
                    "document_id": "doc",
                    "key_id": "0123456789abcdef",
                    "manifest_path": "manifest.json",
                    "output_path": "output.pdf",
                },
                "unfinished": {
                    "status": "issued",
                    "document_id": "doc",
                    "key_id": "0123456789abcdef",
                },
                "wrong-key": {
                    "status": "issued",
                    "document_id": "doc",
                    "key_id": "fedcba9876543210",
                    "manifest_path": "wrong.json",
                    "output_path": "wrong.pdf",
                },
            }
        }
        filtered = _traceable_registry_for_key(
            registry,
            "doc",
            "0123456789abcdef",
        )
        self.assertEqual(set(filtered["issues"]), {"complete"})

    def test_docx_issue_writes_complete_canonical_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.docx"
            source.write_bytes(b"docx-test-source")
            reference = root / "page_001.png"
            image = np.full((640, 640, 3), 245, np.uint8)
            cv2.putText(
                image,
                "DOCX manifest integration",
                (40, 260),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (20, 20, 20),
                2,
                cv2.LINE_AA,
            )
            self.assertTrue(cv2.imwrite(str(reference), image))
            page_record = {
                "page_index": 1,
                "unit_type": "page",
                "width": 640,
                "height": 640,
                "reference_path": str(reference.resolve()),
                "reference_sha256": sha256_file(reference),
            }
            prepared = {
                "reused": False,
                "page_records": [page_record],
                "media_box_points": [480.0, 480.0],
                "render_info": {
                    "source_type": "docx",
                    "render_unit_type": "page",
                    "renderer": "unit_test",
                    "dpi": 96,
                    "page_count": 1,
                },
            }
            registry_path = root / "registry.json"
            output_pdf = root / "watermarked.pdf"
            key = "DOCX-INTEGRATION-KEY"
            with patch(
                "docx_pipeline._prepare_docx_reference_set",
                return_value=prepared,
            ):
                result = embed_docx(
                    source,
                    registry_path,
                    output_pdf,
                    key=key,
                    dpi=96,
                    assets_root=root / "assets",
                    repeat=2,
                    pilot_bits=16,
                    pilot_repeat=1,
                )

            manifest = json.loads(
                Path(result["manifest_path"]).read_text(encoding="utf-8")
            )
            watermark = manifest["watermark"]
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["key_id"], key_id_for_key(key))
            self.assertEqual(watermark["repeat"], 2)
            self.assertEqual(watermark["sync_pilot"]["position_offset"], 280)
            self.assertEqual(
                manifest["pages"][0]["payload_embedding"]["dct_units"],
                280,
            )
            self.assertEqual(
                manifest["pages"][0]["pilot_embedding"]["dct_units"],
                16,
            )
            self.assertTrue(output_pdf.is_file())
            registry = load_document_registry(registry_path)
            issue = registry["issues"][result["trace_token"]]
            self.assertEqual(issue["manifest_path"], str(result["manifest_path"]))

    def test_docx_attach_failure_rolls_back_issue_and_owned_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.docx"
            source.write_bytes(b"docx-rollback-source")
            reference = root / "page_001.png"
            image = np.full((320, 320, 3), 230, np.uint8)
            self.assertTrue(cv2.imwrite(str(reference), image))
            prepared = {
                "reused": False,
                "page_records": [{
                    "page_index": 1,
                    "unit_type": "page",
                    "width": 320,
                    "height": 320,
                    "reference_path": str(reference.resolve()),
                    "reference_sha256": sha256_file(reference),
                }],
                "media_box_points": [240.0, 240.0],
                "render_info": {
                    "source_type": "docx",
                    "render_unit_type": "page",
                    "renderer": "unit_test",
                    "dpi": 96,
                    "page_count": 1,
                },
            }
            registry_path = root / "registry.json"
            output_pdf = root / "watermarked.pdf"
            with patch(
                "docx_pipeline._prepare_docx_reference_set",
                return_value=prepared,
            ), patch(
                "docx_pipeline.attach_issue_artifact",
                side_effect=RuntimeError("forced attach failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "forced attach failure"):
                    embed_docx(
                        source,
                        registry_path,
                        output_pdf,
                        key="DOCX-ROLLBACK-KEY",
                        dpi=96,
                        assets_root=root / "assets",
                        repeat=1,
                        pilot_bits=16,
                        pilot_repeat=1,
                    )

            self.assertFalse(output_pdf.exists())
            self.assertEqual(load_document_registry(registry_path)["issues"], {})


if __name__ == "__main__":
    unittest.main()
