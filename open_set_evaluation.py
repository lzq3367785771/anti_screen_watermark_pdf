#负责在“不知道照片里是否有水印、不知道属于哪个用户”的情况下，对任意照片进行溯源或拒识，并批量统计系统的正确接受率、误报率等关键指标.


import csv
import json
import math
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from native_screen import rectify_native_screen
from synchronization import (
    refine_with_sync_pilot,
    rerank_sync_candidates_with_crc,
    rerank_sync_candidates_with_registry
)
from trace_registry import (
    build_stress_registry,
    load_trace_registry,
    prepare_trace_candidates,
    save_trace_registry
)


DATASET_SCHEMA_VERSION = 1
PROBE_SCHEMA_VERSION = 1
BENCHMARK_SCHEMA_VERSION = 1
NEGATIVE_LABELS = {
    "unregistered",
    "no_watermark",
    "wrong_key",
    "pilot_only",
    "parameter_mismatch",
    "ordinary"
}
DATASET_COLUMNS = [
    "sample_id",
    "photo_path",
    "label",
    "expected_trace_id",
    "reference_experiment",
    "block_size",
    "device",
    "distance_cm",
    "angle_deg",
    "session",
    "notes"
]


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _timestamp_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)
    temporary.replace(path)


def _registered_trace_ids(registry):
    return {
        trace_id
        for trace_id, record in registry.get("records", {}).items()
        if record.get("status", "issued") == "issued"
    }


def decide_crc_registry_attribution(trace_id, registry):
    registered = trace_id in _registered_trace_ids(registry)
    return {
        "accepted": bool(registered),
        "status": (
            "ACCEPTED_CRC_REGISTERED"
            if registered
            else "REJECTED_CRC_VALID_UNREGISTERED"
        ),
        "method": (
            "HAMMING_CRC_REGISTERED"
            if registered
            else "HAMMING_CRC_UNREGISTERED"
        ),
        "trace_id": trace_id,
        "rejection_reasons": [] if registered else ["TRACE_ID_NOT_REGISTERED"]
    }


def _base_probe_report(photo_path, experiment_dir, block_size, registry_count):
    return {
        "schema_version": PROBE_SCHEMA_VERSION,
        "evaluated_at": _now_iso(),
        "photo_path": str(Path(photo_path).resolve()),
        "reference_experiment": str(Path(experiment_dir).resolve()),
        "block_size": int(block_size),
        "registry_count": int(registry_count),
        "geometry": None,
        "synchronization": None,
        "decision": {
            "accepted": False,
            "status": "NOT_EVALUATED",
            "method": None,
            "trace_id": None,
            "rejection_reasons": []
        }
    }


def blind_probe_photo(
        photo_path,
        experiment_dir,
        block_size,
        key,
        registry,
        prepared_candidates=None,
        cache_dir=None
):
    """不读取ground_truth，对任意照片执行开放集溯源或拒识。"""

    started = time.perf_counter()
    photo_path = Path(photo_path).resolve()
    experiment_dir = Path(experiment_dir).resolve()
    variant_dir = experiment_dir / f"variant_{int(block_size)}x{int(block_size)}"
    manifest_path = variant_dir / "manifest.json"
    report = _base_probe_report(
        photo_path,
        experiment_dir,
        block_size,
        len(_registered_trace_ids(registry))
    )

    if not photo_path.is_file():
        report["decision"] = {
            "accepted": False,
            "status": "ERROR_PHOTO_NOT_FOUND",
            "method": None,
            "trace_id": None,
            "rejection_reasons": [str(photo_path)]
        }
        report["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return report
    if not manifest_path.is_file():
        report["decision"] = {
            "accepted": False,
            "status": "ERROR_REFERENCE_MANIFEST_NOT_FOUND",
            "method": None,
            "trace_id": None,
            "rejection_reasons": [str(manifest_path)]
        }
        report["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return report

    manifest = _read_json(manifest_path)
    image = cv2.imread(str(photo_path))
    if image is None:
        report["decision"] = {
            "accepted": False,
            "status": "ERROR_PHOTO_UNREADABLE",
            "method": None,
            "trace_id": None,
            "rejection_reasons": []
        }
        report["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return report

    try:
        restored, _, valid_mask, geometry = rectify_native_screen(
            image,
            manifest["layout"]
        )
    except Exception as exc:
        report["decision"] = {
            "accepted": False,
            "status": "REJECTED_GEOMETRY",
            "method": None,
            "trace_id": None,
            "rejection_reasons": [f"{type(exc).__name__}: {exc}"]
        }
        report["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return report

    report["geometry"] = geometry
    watermark = manifest["watermark"]
    sync_config = watermark.get("sync_pilot", {"enabled": False})
    synchronized, synchronized_mask, synchronization = refine_with_sync_pilot(
        restored,
        valid_mask,
        key,
        int(block_size),
        sync_config
    )
    report["synchronization"] = synchronization
    if not synchronization.get("status", "").startswith("ACCEPTED"):
        report["decision"] = {
            "accepted": False,
            "status": "REJECTED_SYNCHRONIZATION",
            "method": None,
            "trace_id": None,
            "rejection_reasons": [synchronization.get("status")]
        }
        report["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return report

    synchronized, synchronized_mask, crc_rerank = (
        rerank_sync_candidates_with_crc(
            restored,
            valid_mask,
            key,
            int(block_size),
            watermark,
            synchronization
        )
    )
    report["synchronization"]["crc_rerank"] = crc_rerank
    if crc_rerank.get("crc_pass_count", 0) > 0:
        trace_id = crc_rerank["selected"].get("recovered_trace_id")
        report["decision"] = decide_crc_registry_attribution(
            trace_id,
            registry
        )
        report["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return report

    registry_image, registry_mask, registry_rerank = (
        rerank_sync_candidates_with_registry(
            restored,
            valid_mask,
            key,
            int(block_size),
            watermark,
            synchronization,
            registry,
            prepared_candidates=prepared_candidates,
            cache_dir=cache_dir
        )
    )
    del registry_image, registry_mask
    report["synchronization"]["registry_rerank"] = registry_rerank
    selected = registry_rerank.get("selected")
    accepted = bool(registry_rerank.get("accepted", False))
    report["decision"] = {
        "accepted": accepted,
        "status": (
            "ACCEPTED_REGISTRY_ML"
            if accepted
            else "REJECTED_REGISTRY_ML"
        ),
        "method": "REGISTRY_SOFT_ML" if accepted else None,
        "trace_id": selected.get("trace_id") if selected else None,
        "rejection_reasons": registry_rerank.get("rejection_reasons", [])
    }
    report["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
    return report


def create_dataset_manifest(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"样本清单已存在: {path}")
    with path.open("w", newline="", encoding="utf-8-sig") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=DATASET_COLUMNS)
        writer.writeheader()
    return path


def _resolve_manifest_path(value, base_dir):
    path = Path(str(value).strip())
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _read_dataset_manifest(path):
    path = Path(path).resolve()
    with path.open("r", newline="", encoding="utf-8-sig") as file_obj:
        reader = csv.DictReader(file_obj)
        missing = [name for name in DATASET_COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"样本清单缺少字段: {', '.join(missing)}")
        rows = [dict(row) for row in reader]
    return path, rows


def _quantiles(values):
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not finite:
        return {"count": 0}
    percentiles = [0, 10, 25, 50, 75, 90, 95, 99, 100]
    calculated = np.percentile(finite, percentiles)
    return {
        "count": len(finite),
        **{
            f"p{percentile}": float(value)
            for percentile, value in zip(percentiles, calculated)
        }
    }


def _probe_scores(report):
    registry = (report.get("synchronization") or {}).get("registry_rerank") or {}
    selected = registry.get("selected") or {}
    pilot = (report.get("synchronization") or {}).get("best") or {}
    return {
        "z_score": selected.get("z_score"),
        "margin_z": selected.get("margin_z"),
        "normalized_score": selected.get("normalized_score"),
        "hard_match_rate": selected.get("hard_match_rate"),
        "pilot_correlation": pilot.get("normalized_correlation")
    }


def benchmark_open_set(
        manifest_path,
        registry_path,
        key,
        output_dir,
        cache_dir=None
):
    manifest_path, rows = _read_dataset_manifest(manifest_path)
    if not rows:
        raise ValueError("样本清单为空")
    registry_path = Path(registry_path).resolve()
    registry = load_trace_registry(registry_path)
    if not _registered_trace_ids(registry):
        raise ValueError("注册表中没有已发行TraceID")
    cache_dir = Path(cache_dir).resolve() if cache_dir else registry_path.parent / ".trace_cache"
    prepared = prepare_trace_candidates(
        registry,
        null_candidate_count=4096,
        cache_dir=cache_dir
    )
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    sample_reports_dir = output_dir / "sample_reports"
    sample_reports_dir.mkdir()

    results = []
    seen_sample_ids = set()
    for row_index, row in enumerate(rows, start=1):
        sample_id = row["sample_id"].strip() or f"sample_{row_index:05d}"
        if not re.fullmatch(r"[A-Za-z0-9._-]+", sample_id):
            raise ValueError(
                f"sample_id只能包含字母、数字、点、下划线和连字符: {sample_id}"
            )
        if sample_id in seen_sample_ids:
            raise ValueError(f"sample_id重复: {sample_id}")
        seen_sample_ids.add(sample_id)
        label = row["label"].strip().lower()
        if label != "positive" and label not in NEGATIVE_LABELS:
            raise ValueError(f"{sample_id}的label非法: {label}")
        photo_path = _resolve_manifest_path(row["photo_path"], manifest_path.parent)
        experiment_dir = _resolve_manifest_path(
            row["reference_experiment"],
            manifest_path.parent
        )
        block_size = int(row["block_size"])
        report = blind_probe_photo(
            photo_path,
            experiment_dir,
            block_size,
            key,
            registry,
            prepared_candidates=prepared,
            cache_dir=cache_dir
        )
        expected = row["expected_trace_id"].strip().lower() or None
        accepted = bool(report["decision"]["accepted"])
        predicted = report["decision"].get("trace_id")
        decision_status = report["decision"]["status"]
        if decision_status.startswith("ERROR_"):
            outcome = "ERROR_INFRASTRUCTURE"
        elif label == "positive":
            if expected is None:
                outcome = "ERROR_MISSING_EXPECTED_TRACE_ID"
            elif accepted and predicted == expected:
                outcome = "TRUE_ACCEPT"
            elif accepted:
                outcome = "WRONG_ACCEPT"
            else:
                outcome = "FALSE_REJECT"
        else:
            outcome = "FALSE_ACCEPT" if accepted else "TRUE_REJECT"
        scores = _probe_scores(report)
        result = {
            "sample_id": sample_id,
            "label": label,
            "expected_trace_id": expected,
            "predicted_trace_id": predicted,
            "accepted": accepted,
            "decision_status": decision_status,
            "outcome": outcome,
            "block_size": block_size,
            "device": row["device"],
            "distance_cm": row["distance_cm"],
            "angle_deg": row["angle_deg"],
            "session": row["session"],
            "elapsed_ms": float(report["elapsed_ms"]),
            **scores
        }
        results.append(result)
        _write_json(sample_reports_dir / f"{sample_id}.json", {
            "dataset_row": row,
            "classification": result,
            "probe": report
        })

    outcome_counts = Counter(result["outcome"] for result in results)
    valid_results = [
        result for result in results
        if not result["outcome"].startswith("ERROR_")
    ]
    error_count = len(results) - len(valid_results)
    positive_count = sum(
        result["label"] == "positive" for result in valid_results
    )
    negative_count = len(valid_results) - positive_count
    true_accepts = outcome_counts["TRUE_ACCEPT"]
    false_rejects = outcome_counts["FALSE_REJECT"]
    wrong_accepts = outcome_counts["WRONG_ACCEPT"]
    true_rejects = outcome_counts["TRUE_REJECT"]
    false_accepts = outcome_counts["FALSE_ACCEPT"]
    zero_fa_upper_95 = (
        1.0 - math.pow(0.05, 1.0 / negative_count)
        if negative_count > 0 and false_accepts == 0
        else None
    )

    positive_results = [
        item for item in valid_results if item["label"] == "positive"
    ]
    negative_results = [
        item for item in valid_results if item["label"] != "positive"
    ]
    threshold_report = {}
    for score_name in ["z_score", "margin_z", "normalized_score", "pilot_correlation"]:
        threshold_report[score_name] = {
            "positive": _quantiles([item[score_name] for item in positive_results]),
            "negative": _quantiles([item[score_name] for item in negative_results])
        }

    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "dataset_manifest": str(manifest_path),
        "trace_registry": str(registry_path),
        "registry_count": int(prepared["registered_count"]),
        "codeword_cache": {
            "path": prepared.get("cache_path"),
            "hit": bool(prepared.get("cache_hit", False)),
            "key": prepared.get("cache_key")
        },
        "sample_count": len(results),
        "valid_sample_count": len(valid_results),
        "error_count": error_count,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "outcomes": dict(outcome_counts),
        "metrics": {
            "correct_accept_rate": (
                true_accepts / positive_count if positive_count else None
            ),
            "false_reject_rate": (
                false_rejects / positive_count if positive_count else None
            ),
            "wrong_accept_rate": (
                wrong_accepts / positive_count if positive_count else None
            ),
            "true_reject_rate": (
                true_rejects / negative_count if negative_count else None
            ),
            "false_accept_rate": (
                false_accepts / negative_count if negative_count else None
            ),
            "zero_false_accept_upper_95": zero_fa_upper_95
        },
        "threshold_report": threshold_report,
        "status_counts": dict(Counter(
            result["decision_status"] for result in results
        )),
        "mean_elapsed_ms": float(np.mean([
            result["elapsed_ms"] for result in results
        ]))
    }
    _write_json(output_dir / "benchmark_summary.json", summary)
    with (output_dir / "sample_results.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    return summary, results


def registry_stress_command(args):
    base = load_trace_registry(Path(args.base).resolve())
    stress = build_stress_registry(base, args.size, seed=args.seed)
    output = Path(args.output).resolve()
    saved = save_trace_registry(output, stress)
    print("Stress registry:", output)
    print("Records:", len(saved["records"]))
    print("WARNING: 该文件仅用于压力测试，不可作为正式发行注册表")
    return saved


def dataset_init_command(args):
    path = create_dataset_manifest(Path(args.output).resolve())
    print("Dataset manifest:", path)
    print("Labels: positive,", ", ".join(sorted(NEGATIVE_LABELS)))
    return path


def probe_command(args):
    registry_path = Path(args.trace_registry).resolve()
    registry = load_trace_registry(registry_path)
    cache_dir = (
        Path(args.cache_dir).resolve()
        if args.cache_dir
        else registry_path.parent / ".trace_cache"
    )
    prepared = None
    if _registered_trace_ids(registry):
        prepared = prepare_trace_candidates(
            registry,
            null_candidate_count=4096,
            cache_dir=cache_dir
        )
    report = blind_probe_photo(
        args.photo,
        args.reference_experiment,
        args.block_size,
        args.key,
        registry,
        prepared_candidates=prepared,
        cache_dir=cache_dir
    )
    if args.output:
        output = Path(args.output).resolve()
    else:
        output = Path("probe_results") / f"probe_{_timestamp_id()}.json"
        output = output.resolve()
    _write_json(output, report)
    print("Probe:", report["decision"]["status"])
    print("Accepted:", report["decision"]["accepted"])
    print("TraceID:", report["decision"].get("trace_id"))
    print("Saved:", output)
    return report


def benchmark_command(args):
    output = (
        Path(args.output).resolve()
        if args.output
        else Path("benchmark_results") / f"v193_{_timestamp_id()}"
    )
    summary, _ = benchmark_open_set(
        args.dataset,
        args.trace_registry,
        args.key,
        output,
        cache_dir=args.cache_dir
    )
    metrics = summary["metrics"]
    print("Open-set benchmark:", output)
    print("Samples:", summary["sample_count"])
    print("Registry:", summary["registry_count"])
    print("Correct accept rate:", metrics["correct_accept_rate"])
    print("False accept rate:", metrics["false_accept_rate"])
    print("Wrong accept rate:", metrics["wrong_accept_rate"])
    if metrics["zero_false_accept_upper_95"] is not None:
        print(
            "Zero-FA 95% upper bound:",
            metrics["zero_false_accept_upper_95"]
        )
    return summary
