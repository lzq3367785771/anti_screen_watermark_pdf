import csv
import gc
import tempfile
import unittest
from pathlib import Path

import numpy as np

from open_set_evaluation import (
    DATASET_COLUMNS,
    create_dataset_manifest,
    decide_crc_registry_attribution
)
from trace_registry import (
    build_stress_registry,
    prepare_trace_candidates,
    score_trace_candidates,
    trace_id_to_codeword
)


TRUE_TRACE_ID = "dd856b4d12354d804f10af98fa9bd679"


class V193OpenSetTests(unittest.TestCase):
    def base_registry(self):
        return {
            "schema_version": 1,
            "records": {
                TRUE_TRACE_ID: {
                    "trace_id": TRUE_TRACE_ID,
                    "status": "issued"
                }
            }
        }

    def test_crc_requires_registered_trace_id(self):
        accepted = decide_crc_registry_attribution(
            TRUE_TRACE_ID,
            self.base_registry()
        )
        rejected = decide_crc_registry_attribution(
            "55f1f22f1497a9d90eddbcb8f8bee928",
            self.base_registry()
        )

        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["status"], "ACCEPTED_CRC_REGISTERED")
        self.assertFalse(rejected["accepted"])
        self.assertEqual(
            rejected["status"],
            "REJECTED_CRC_VALID_UNREGISTERED"
        )

    def test_stress_registry_is_deterministic_and_preserves_base(self):
        first = build_stress_registry(
            self.base_registry(),
            100,
            seed="unit-test"
        )
        second = build_stress_registry(
            self.base_registry(),
            100,
            seed="unit-test"
        )

        self.assertEqual(len(first["records"]), 100)
        self.assertIn(TRUE_TRACE_ID, first["records"])
        self.assertEqual(first["records"].keys(), second["records"].keys())
        stress_records = [
            record for trace_id, record in first["records"].items()
            if trace_id != TRUE_TRACE_ID
        ]
        self.assertTrue(all(
            record["environment"] == "stress-test-only"
            for record in stress_records
        ))

    def test_int8_codeword_cache_and_chunked_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = build_stress_registry(
                self.base_registry(),
                100,
                seed="cache-test"
            )
            prepared_first = prepare_trace_candidates(
                registry,
                null_candidate_count=128,
                cache_dir=temporary
            )
            prepared_second = prepare_trace_candidates(
                registry,
                null_candidate_count=128,
                cache_dir=temporary
            )
            codeword = trace_id_to_codeword(TRUE_TRACE_ID)
            soft_scores = (codeword.astype(np.float32) * 2.0 - 1.0).tolist()
            scored = score_trace_candidates(
                soft_scores,
                prepared_second,
                chunk_size=17,
                include_registered_records=False
            )
            true_index = prepared_second["trace_ids"].index(TRUE_TRACE_ID)

            self.assertEqual(prepared_first["bit_matrix"].dtype, np.int8)
            self.assertFalse(prepared_first["cache_hit"])
            self.assertTrue(prepared_second["cache_hit"])
            self.assertEqual(
                prepared_first["cache_key"],
                prepared_second["cache_key"]
            )
            self.assertGreater(scored["registered_z_scores"][true_index], 15.0)
            self.assertAlmostEqual(
                scored["registered_normalized_scores"][true_index],
                1.0,
                places=5
            )
            del scored
            del prepared_first
            del prepared_second
            gc.collect()

    def test_dataset_manifest_has_stable_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dataset.csv"
            create_dataset_manifest(path)
            with path.open("r", newline="", encoding="utf-8-sig") as file_obj:
                reader = csv.reader(file_obj)
                header = next(reader)
            self.assertEqual(header, DATASET_COLUMNS)
            with self.assertRaises(FileExistsError):
                create_dataset_manifest(path)


if __name__ == "__main__":
    unittest.main()
