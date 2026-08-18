import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from trace_registry import (
    decide_trace_attribution,
    load_trace_registry,
    prepare_trace_candidates,
    score_trace_candidates,
    sync_trace_registry,
    trace_id_to_codeword
)


TRUE_TRACE_ID = "dd856b4d12354d804f10af98fa9bd679"


class TraceRegistryTests(unittest.TestCase):
    def make_registry(self, count=32):
        records = {
            TRUE_TRACE_ID: {
                "trace_id": TRUE_TRACE_ID,
                "status": "issued"
            }
        }
        for index in range(count - 1):
            trace_id = f"{index + 1:032x}"
            records[trace_id] = {
                "trace_id": trace_id,
                "status": "issued"
            }
        return {"schema_version": 1, "records": records}

    def test_soft_ml_recovers_trace_with_twenty_percent_hard_errors(self):
        prepared = prepare_trace_candidates(
            self.make_registry(),
            null_candidate_count=4096
        )
        codeword = trace_id_to_codeword(TRUE_TRACE_ID)
        signs = codeword.astype(np.float32) * 2.0 - 1.0
        rng = np.random.default_rng(20260817)
        error_indices = rng.choice(len(signs), size=51, replace=False)
        signs[error_indices] *= -1.0
        soft_scores = (signs * 2.0).tolist()

        scored = score_trace_candidates(soft_scores, prepared)
        best_by_trace = {
            item["trace_id"]: {
                **item,
                "observed_bits": scored["observed_bits"]
            }
            for item in scored["registered_scores"]
        }
        decision = decide_trace_attribution(
            best_by_trace,
            scored["null_best_z"]
        )

        self.assertTrue(decision["accepted"])
        self.assertEqual(decision["selected"]["trace_id"], TRUE_TRACE_ID)
        self.assertGreater(decision["selected"]["margin_z"], 1.5)

    def test_random_soft_information_is_rejected(self):
        prepared = prepare_trace_candidates(
            self.make_registry(),
            null_candidate_count=4096
        )
        rng = np.random.default_rng(9012)
        soft_scores = rng.normal(0.0, 1.0, 252).tolist()
        scored = score_trace_candidates(soft_scores, prepared)
        best_by_trace = {
            item["trace_id"]: {
                **item,
                "observed_bits": scored["observed_bits"]
            }
            for item in scored["registered_scores"]
        }
        decision = decide_trace_attribution(
            best_by_trace,
            scored["null_best_z"]
        )

        self.assertFalse(decision["accepted"])
        self.assertTrue(decision["rejection_reasons"])

    def test_too_many_erasures_are_rejected(self):
        prepared = prepare_trace_candidates(
            self.make_registry(),
            null_candidate_count=512
        )
        codeword = trace_id_to_codeword(TRUE_TRACE_ID)
        soft_scores = [
            float(bit * 2 - 1) if index < 100 else None
            for index, bit in enumerate(codeword)
        ]
        scored = score_trace_candidates(soft_scores, prepared)
        best_by_trace = {
            item["trace_id"]: {
                **item,
                "observed_bits": scored["observed_bits"]
            }
            for item in scored["registered_scores"]
        }
        decision = decide_trace_attribution(
            best_by_trace,
            scored["null_best_z"]
        )

        self.assertFalse(decision["accepted"])
        self.assertIn(
            "INSUFFICIENT_OBSERVED_BITS",
            decision["rejection_reasons"]
        )

    def test_registry_build_migrates_legacy_and_experiment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_path = root / "trace_db.json"
            legacy_id = "f5477eab3ec78f885cf2129c8b838622"
            legacy_path.write_text(
                json.dumps({legacy_id: {"user": "User-001"}}),
                encoding="utf-8"
            )
            experiment_dir = root / "experiments" / "sample"
            experiment_dir.mkdir(parents=True)
            (experiment_dir / "ground_truth.json").write_text(
                json.dumps({
                    "trace_id": TRUE_TRACE_ID,
                    "experiment_id": "sample"
                }),
                encoding="utf-8"
            )
            output_path = root / "trace_registry.json"

            registry, stats = sync_trace_registry(
                output_path,
                experiments_roots=[root / "experiments"],
                legacy_db_paths=[legacy_path]
            )
            loaded = load_trace_registry(output_path)

            self.assertEqual(stats["record_count"], 2)
            self.assertIn(TRUE_TRACE_ID, registry["records"])
            self.assertIn(legacy_id, loaded["records"])


if __name__ == "__main__":
    unittest.main()
