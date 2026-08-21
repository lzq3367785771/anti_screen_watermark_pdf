import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from document_registry import (
    DOCUMENT_CODEWORD_BITS,
    decode_document_bits,
    decode_document_soft_scores,
    encode_document_token,
    issue_document_trace,
    normalize_watermark_number,
    register_document,
    retire_document_issue,
    score_registered_tokens,
    infer_source_type,
    default_render_unit_type,
)
from pdf_pipeline import (
    _manifest_for_document,
    _orb_homography,
    aggregate_trimmed_repeat_scores,
    build_local_partition_masks,
    embed_bits_adaptive,
    fuse_partition_soft_scores,
)
from watermark import extract_watermark_with_erasure


class DocumentRegistryTests(unittest.TestCase):
    def test_document_source_type_inference(self):

        self.assertEqual(
            infer_source_type("example.pdf"),
            "pdf",
        )

        self.assertEqual(
            infer_source_type("example.docx"),
            "docx",
        )

        self.assertEqual(
            infer_source_type("example.pptx"),
            "pptx",
        )

        self.assertEqual(
            infer_source_type("example.xlsx"),
            "xlsx",
        )

        self.assertEqual(
            infer_source_type(
                "example.bin",
                source_type="word",
            ),
            "docx",
        )

        self.assertEqual(
            default_render_unit_type("pdf"),
            "page",
        )

        self.assertEqual(
            default_render_unit_type("docx"),
            "page",
        )

        self.assertEqual(
            default_render_unit_type("pptx"),
            "slide",
        )

        self.assertEqual(
            default_render_unit_type("xlsx"),
            "sheet_page",
        )
    
    
    
    def test_register_office_document_metadata(self):

        with tempfile.TemporaryDirectory() as temporary:

            root = Path(temporary)

            source = root / "meeting.pptx"

            # 这里只测试注册库，不解析真正PPTX。
            source.write_bytes(
                b"dummy-pptx-registry-test"
            )

            reference = root / "slide_001.png"

            cv2.imwrite(
                str(reference),
                np.full(
                    (720, 1280, 3),
                    255,
                    np.uint8,
                ),
            )

            registry_path = root / "registry.json"

            document_id, document = register_document(
                registry_path,

                source,

                [{
                    "page_index": 1,
                    "width": 1280,
                    "height": 720,
                    "reference_path": str(reference),
                    "reference_sha256": "test",
                }],

                96,

                [960, 540],

                root,
            )

            self.assertTrue(document_id)

            self.assertEqual(
                document["source_type"],
                "pptx",
            )

            self.assertEqual(
                document["source_extension"],
                ".pptx",
            )

            self.assertEqual(
                document["render_unit_type"],
                "slide",
            )

            self.assertEqual(
                document["pages"][0]["unit_type"],
                "slide",
            )

            self.assertEqual(
                document["pages"][0]["page_index"],
                1,
            )





    def test_user_watermark_number_is_normalized_and_unique(self):
        self.assertEqual(normalize_watermark_number(" wm-2026-001 "), "WM-2026-001")
        with self.assertRaises(ValueError):
            normalize_watermark_number("含中文")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            source.write_bytes(b"watermark-number-source")
            reference = root / "page.png"
            cv2.imwrite(str(reference), np.full((80, 64, 3), 255, np.uint8))
            registry_path = root / "registry.json"
            document_id, _ = register_document(
                registry_path,
                source,
                [{
                    "page_index": 1,
                    "width": 64,
                    "height": 80,
                    "reference_path": str(reference),
                    "reference_sha256": "test",
                }],
                96,
                [48, 60],
                root,
            )
            issue = issue_document_trace(
                registry_path,
                document_id,
                watermark_number="wm-2026-001",
            )
            self.assertEqual(issue["watermark_number"], "WM-2026-001")
            with self.assertRaisesRegex(ValueError, "水印号码已存在"):
                issue_document_trace(
                    registry_path,
                    document_id,
                    watermark_number="WM-2026-001",
                )
            retired = retire_document_issue(registry_path, issue["trace_token"])
            self.assertEqual(retired["status"], "deleted")
            with registry_path.open("r", encoding="utf-8") as file_obj:
                registry = json.load(file_obj)
            scores = [
                1.0 if bit else -1.0
                for bit in encode_document_token(issue["trace_token"])
            ]
            decision = score_registered_tokens(
                scores, registry, document_id=document_id
            )
            self.assertEqual(decision["status"], "REJECTED_EMPTY_CANDIDATES")

    def test_document_token_codec_round_trip(self):
        token = "0123456789abcdef"
        encoded = encode_document_token(token)
        self.assertEqual(DOCUMENT_CODEWORD_BITS, 140)
        self.assertEqual(len(encoded), DOCUMENT_CODEWORD_BITS)
        decoded = decode_document_bits(encoded)
        self.assertTrue(decoded["crc_pass"])
        self.assertEqual(decoded["trace_token"], token)
        scores = [8.0 if bit else -8.0 for bit in encoded]
        soft = decode_document_soft_scores(scores)
        self.assertTrue(soft["crc_pass"])
        self.assertEqual(soft["trace_token"], token)

    def test_registry_scores_exact_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            source.write_bytes(b"minimal-test-source")
            reference = root / "page_001.png"
            cv2.imwrite(str(reference), np.full((800, 600, 3), 255, np.uint8))
            registry_path = root / "registry.json"
            document_id, _ = register_document(
                registry_path,
                source,
                [{
                    "page_index": 1,
                    "width": 600,
                    "height": 800,
                    "reference_path": str(reference),
                    "reference_sha256": "test",
                }],
                100,
                [432, 576],
                root,
            )
            issue = issue_document_trace(
                registry_path,
                document_id,
                trace_token="0123456789abcdef",
            )
            with registry_path.open("r", encoding="utf-8") as file_obj:
                registry = json.load(file_obj)
            codeword = encode_document_token(issue["trace_token"])
            scores = [10.0 if bit else -10.0 for bit in codeword]
            decision = score_registered_tokens(scores, registry, document_id=document_id)
            self.assertTrue(decision["accepted"])
            self.assertEqual(decision["selected"]["trace_token"], issue["trace_token"])


class DocumentCarrierTests(unittest.TestCase):
    def test_local_partition_masks_cover_page_without_quadrant_overlap(self):
        mask = np.full((80, 64), 255, np.uint8)
        partitions = build_local_partition_masks(mask, block_size=8)
        self.assertEqual(len(partitions), 8)
        quadrants = [item for item in partitions if item["fusion_member"]]
        self.assertEqual(len(quadrants), 4)
        accumulated = np.zeros_like(mask, dtype=np.uint16)
        for item in quadrants:
            accumulated += (item["mask"] >= 128).astype(np.uint16)
            self.assertAlmostEqual(item["page_coverage"], 0.25, places=3)
        self.assertTrue(np.all(accumulated == 1))

    def test_partition_fusion_downweights_corrupted_low_pilot_tile(self):
        token = "0123456789abcdef"
        encoded = encode_document_token(token)
        good = [9.0 if bit else -9.0 for bit in encoded]
        corrupted = [-value for value in good]
        fused, diagnostics = fuse_partition_soft_scores([
            {
                "name": "good",
                "scores": good,
                "pilot": {
                    "observed_bits": 64,
                    "normalized_correlation": 0.85,
                    "match_rate": 0.90,
                },
            },
            {
                "name": "corrupted",
                "scores": corrupted,
                "pilot": {
                    "observed_bits": 16,
                    "normalized_correlation": 0.02,
                    "match_rate": 0.51,
                },
            },
        ])
        decoded = decode_document_soft_scores(fused)
        self.assertTrue(decoded["crc_pass"])
        self.assertEqual(decoded["trace_token"], token)
        self.assertEqual(diagnostics["observed_bits"], DOCUMENT_CODEWORD_BITS)

    def test_trimmed_mean_rejects_repeat_outliers(self):
        details = {
            "min_valid_repeats": 3,
            "repeat_scores": [
                [-1000.0, -500.0, 8.0, 9.0, 10.0, 11.0, 900.0, 1200.0],
                [None, -900.0, -10.0, -9.0, -8.0, -7.0, 1000.0, None],
            ],
        }
        scores, aggregation = aggregate_trimmed_repeat_scores(
            details, trim_ratio=0.25
        )
        self.assertGreater(scores[0], 0.0)
        self.assertLess(scores[1], 0.0)
        self.assertEqual(aggregation["method"], "trimmed_mean")
        self.assertEqual(aggregation["observed_bits"], 2)

    def test_latest_document_manifest_is_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_path = root / "old.manifest.json"
            new_path = root / "new.manifest.json"
            base_manifest = {
                "schema_version": 2,
                "key_id": "0123456789abcdef",
                "page_count": 1,
                "watermark": {
                    "block_size": 8,
                    "bit_count": DOCUMENT_CODEWORD_BITS,
                    "alpha": 42.0,
                    "repeat": 16,
                    "sync_pilot": {
                        "enabled": True,
                        "bit_count": 64,
                        "repeat": 6,
                        "alpha": 78.0,
                        "position_offset": 2240,
                    },
                },
                "pages": [{"page_index": 1}],
            }
            old_path.write_text(
                json.dumps({**base_manifest, "trace_token": "old"}),
                encoding="utf-8",
            )
            new_path.write_text(
                json.dumps({**base_manifest, "trace_token": "new"}),
                encoding="utf-8",
            )
            registry = {
                "issues": {
                    "old": {
                        "document_id": "doc",
                        "issued_at": "2026-08-18T09:00:00+08:00",
                        "manifest_path": str(old_path),
                    },
                    "new": {
                        "document_id": "doc",
                        "issued_at": "2026-08-18T10:00:00+08:00",
                        "manifest_path": str(new_path),
                    },
                }
            }
            selected_path, manifest = _manifest_for_document(registry, "doc")
            self.assertEqual(selected_path, new_path)
            self.assertEqual(manifest["trace_token"], "new")

    def test_adaptive_dct_round_trip(self):
        height, width = 960, 720
        image = np.full((height, width, 3), 255, np.uint8)
        for y in range(80, 880, 42):
            cv2.putText(
                image,
                f"NiMark document watermark line {y}",
                (45, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (30, 30, 30),
                1,
                cv2.LINE_AA,
            )
        token = "0123456789abcdef"
        encoded = encode_document_token(token)
        watermarked, _ = embed_bits_adaptive(
            image,
            encoded,
            "unit-test-key",
            alpha=55.0,
            repeat=8,
            block_size=8,
        )
        mask = np.full((height, width), 255, np.uint8)
        bits, scores, _ = extract_watermark_with_erasure(
            watermarked,
            mask,
            bit_count=len(encoded),
            key="unit-test-key",
            repeat=8,
            block_size=8,
        )
        self.assertEqual(bits, encoded)
        decoded = decode_document_soft_scores(scores)
        self.assertTrue(decoded["crc_pass"])
        self.assertEqual(decoded["trace_token"], token)

    def test_orb_recovers_full_page_perspective(self):
        reference = np.full((1000, 760, 3), 255, np.uint8)
        for row in range(60, 940, 45):
            cv2.putText(
                reference,
                f"Section {row:04d} watermark alignment",
                (35, row),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 0, 0),
                1,
                cv2.LINE_AA,
            )
        cv2.rectangle(reference, (420, 120), (700, 360), (30, 80, 180), 5)
        cv2.circle(reference, (560, 240), 80, (180, 80, 30), 5)
        source = np.float32([[0, 0], [759, 0], [759, 999], [0, 999]])
        destination = np.float32([[110, 80], [840, 35], [900, 1110], [45, 1060]])
        forward = cv2.getPerspectiveTransform(source, destination)
        photo = cv2.warpPerspective(
            reference,
            forward,
            (960, 1180),
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(80, 80, 80),
        )
        result = _orb_homography(photo, reference)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result["inliers"], 12)
        identity = result["homography"] @ forward
        identity = identity / identity[2, 2]
        self.assertLess(float(np.max(np.abs(identity - np.eye(3)))), 4.0)


if __name__ == "__main__":
    unittest.main()
