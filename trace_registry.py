import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from codec import build_payload, bytes_to_bits, hamming_encode


REGISTRY_SCHEMA_VERSION = 1
NULL_MODEL_VERSION = "v1.9.2-null-trace-v1"
CODEWORD_CACHE_VERSION = "hamming74-crc16-v1"


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_trace_id(trace_id):
    value = str(trace_id).strip().lower()
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"非法TraceID: {trace_id}") from exc
    if len(raw) != 16:
        raise ValueError("TraceID必须是32位十六进制字符串")
    return value


def empty_registry():
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "records": {}
    }


def load_trace_registry(path):
    """读取V1.9.2注册表，同时兼容旧trace_db.json字典格式。"""

    path = Path(path)
    if not path.is_file():
        return empty_registry()

    with path.open("r", encoding="utf-8") as file_obj:
        raw = json.load(file_obj)

    source_records = raw.get("records", raw) if isinstance(raw, dict) else {}
    records = {}
    for trace_id, metadata in source_records.items():
        try:
            normalized = normalize_trace_id(trace_id)
        except ValueError:
            continue
        record = dict(metadata) if isinstance(metadata, dict) else {}
        record["trace_id"] = normalized
        record.setdefault("status", "issued")
        records[normalized] = record

    result = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "updated_at": raw.get("updated_at", _now_iso()) if isinstance(raw, dict) else _now_iso(),
        "records": records
    }
    if isinstance(raw, dict) and raw.get("registry_kind"):
        result["registry_kind"] = raw["registry_kind"]
    return result


def save_trace_registry(path, registry):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "records": registry.get("records", {})
    }
    if registry.get("registry_kind"):
        data["registry_kind"] = registry["registry_kind"]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)
    temporary.replace(path)
    return data


def register_trace_id(path, trace_id, metadata=None):
    trace_id = normalize_trace_id(trace_id)
    registry = load_trace_registry(path)
    existing = registry["records"].get(trace_id, {})
    record = {
        **existing,
        **(dict(metadata) if metadata else {}),
        "trace_id": trace_id,
        "status": "issued"
    }
    record.setdefault("registered_at", _now_iso())
    registry["records"][trace_id] = record
    return save_trace_registry(path, registry)


def sync_trace_registry(output_path, experiments_roots=(), legacy_db_paths=()):
    """把历史实验真值和旧trace_db合并到V1.9.2发行注册表。"""

    registry = load_trace_registry(output_path)
    imported_experiments = 0
    imported_legacy = 0

    for legacy_path in legacy_db_paths:
        legacy_path = Path(legacy_path)
        if not legacy_path.is_file():
            continue
        legacy = load_trace_registry(legacy_path)
        for trace_id, record in legacy["records"].items():
            merged = {
                **record,
                "trace_id": trace_id,
                "status": "issued",
                "registry_source": str(legacy_path.resolve())
            }
            registry["records"].setdefault(trace_id, merged)
            imported_legacy += 1

    seen_ground_truths = set()
    for root in experiments_roots:
        root = Path(root)
        if not root.exists():
            continue
        paths = [root] if root.name == "ground_truth.json" else root.rglob("ground_truth.json")
        for ground_truth_path in paths:
            resolved = str(ground_truth_path.resolve())
            if resolved in seen_ground_truths:
                continue
            seen_ground_truths.add(resolved)
            try:
                with ground_truth_path.open("r", encoding="utf-8") as file_obj:
                    ground_truth = json.load(file_obj)
                trace_id = normalize_trace_id(ground_truth["trace_id"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            existing = registry["records"].get(trace_id, {})
            registry["records"][trace_id] = {
                **existing,
                "trace_id": trace_id,
                "status": "issued",
                "experiment_id": ground_truth.get("experiment_id"),
                "created_at": ground_truth.get("created_at"),
                "key_id": ground_truth.get("key_id"),
                "registry_source": resolved
            }
            imported_experiments += 1

    saved = save_trace_registry(output_path, registry)
    return saved, {
        "record_count": len(saved["records"]),
        "experiment_records_seen": imported_experiments,
        "legacy_records_seen": imported_legacy
    }


def build_stress_registry(base_registry, target_size, seed="v193-stress"):
    """生成独立压力注册库；不会修改正式注册表。"""

    target_size = int(target_size)
    if target_size < 1:
        raise ValueError("压力注册库大小必须至少为1")
    records = {
        trace_id: dict(record)
        for trace_id, record in base_registry.get("records", {}).items()
        if record.get("status", "issued") == "issued"
    }
    if len(records) > target_size:
        raise ValueError(
            f"正式注册表已有{len(records)}条，不能缩小到{target_size}条"
        )

    index = 0
    while len(records) < target_size:
        trace_id = hashlib.sha256(
            f"v1.9.3:{seed}:{index}".encode("utf-8")
        ).hexdigest()[:32]
        index += 1
        if trace_id in records:
            continue
        records[trace_id] = {
            "trace_id": trace_id,
            "status": "issued",
            "environment": "stress-test-only",
            "seed": str(seed)
        }
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "registry_kind": "stress-test-only",
        "records": records
    }


def trace_id_to_codeword(trace_id):
    trace_id = normalize_trace_id(trace_id)
    return np.asarray(
        hamming_encode(bytes_to_bits(build_payload(trace_id))),
        dtype=np.int8
    )


def _null_trace_ids(count, excluded):
    excluded = set(excluded)
    generated = []
    index = 0
    while len(generated) < int(count):
        digest = hashlib.sha256(
            f"{NULL_MODEL_VERSION}:{index}".encode("utf-8")
        ).hexdigest()[:32]
        index += 1
        if digest in excluded:
            continue
        generated.append(digest)
    return generated


def _candidate_cache_key(trace_ids, null_ids):
    digest = hashlib.sha256()
    digest.update(CODEWORD_CACHE_VERSION.encode("utf-8"))
    for trace_id in trace_ids:
        digest.update(trace_id.encode("ascii"))
    digest.update(b"|null|")
    for trace_id in null_ids:
        digest.update(trace_id.encode("ascii"))
    return digest.hexdigest()


def prepare_trace_candidates(
        registry,
        null_candidate_count=4096,
        cache_dir=None
):
    active_records = {
        trace_id: record
        for trace_id, record in registry.get("records", {}).items()
        if record.get("status", "issued") == "issued"
    }
    trace_ids = sorted(active_records)
    if not trace_ids:
        raise ValueError("TraceID注册表中没有已发行记录")

    null_ids = _null_trace_ids(null_candidate_count, trace_ids)
    all_ids = trace_ids + null_ids
    cache_key = _candidate_cache_key(trace_ids, null_ids)
    cache_path = None

    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"trace_codewords_{cache_key[:20]}.npy"

    expected_shape = (len(all_ids), 252)
    if cache_path is not None and cache_path.is_file():
        bit_matrix = np.load(cache_path, mmap_mode="r")
        if bit_matrix.shape != expected_shape or bit_matrix.dtype != np.int8:
            raise RuntimeError(f"TraceID码字缓存格式异常: {cache_path}")
        cache_hit = True
    else:
        if cache_path is not None:
            bit_matrix = np.lib.format.open_memmap(
                cache_path,
                mode="w+",
                dtype=np.int8,
                shape=expected_shape
            )
        else:
            bit_matrix = np.empty(expected_shape, dtype=np.int8)
        for index, trace_id in enumerate(all_ids):
            bit_matrix[index] = trace_id_to_codeword(trace_id) * 2 - 1
        if cache_path is not None:
            bit_matrix.flush()
            del bit_matrix
            bit_matrix = np.load(cache_path, mmap_mode="r")
        cache_hit = False

    return {
        "trace_ids": trace_ids,
        "records": active_records,
        "registered_count": len(trace_ids),
        "null_candidate_count": len(null_ids),
        "bit_matrix": bit_matrix,
        "cache_key": cache_key,
        "cache_path": str(cache_path.resolve()) if cache_path else None,
        "cache_hit": cache_hit
    }


def score_trace_candidates(
        soft_scores,
        prepared,
        chunk_size=16384,
        include_registered_records=True
):
    if len(soft_scores) != prepared["bit_matrix"].shape[1]:
        raise ValueError("软信息长度与TraceID码字长度不一致")

    observed = np.asarray([
        score is not None and np.isfinite(float(score))
        for score in soft_scores
    ], dtype=bool)
    observed_count = int(np.sum(observed))
    if observed_count == 0:
        return {
            "observed_bits": 0,
            "registered_scores": [],
            "registered_z_scores": np.full(
                prepared["registered_count"], -np.inf, dtype=np.float32
            ),
            "registered_normalized_scores": np.full(
                prepared["registered_count"], -np.inf, dtype=np.float32
            ),
            "registered_hard_match_rates": np.zeros(
                prepared["registered_count"], dtype=np.float32
            ),
            "null_best_z": -np.inf,
            "normalizer_l1": 0.0,
            "normalizer_l2": 0.0
        }

    values = np.asarray([
        float(score) if keep else 0.0
        for score, keep in zip(soft_scores, observed)
    ], dtype=np.float32)
    l1 = float(np.sum(np.abs(values[observed])))
    l2 = float(np.sqrt(np.sum(np.square(values[observed]))))
    registered_count = int(prepared["registered_count"])
    total_count = prepared["bit_matrix"].shape[0]
    registered_z = np.empty(registered_count, dtype=np.float32)
    registered_normalized = np.empty(registered_count, dtype=np.float32)
    registered_hard = np.empty(registered_count, dtype=np.float32)
    null_best_z = -np.inf
    observed_values = values[observed]
    observed_signs = observed_values > 0

    for start in range(0, total_count, int(chunk_size)):
        end = min(total_count, start + int(chunk_size))
        matrix_chunk = np.asarray(
            prepared["bit_matrix"][start:end, observed],
            dtype=np.float32
        )
        raw_chunk = matrix_chunk @ observed_values
        z_chunk = raw_chunk / max(l2, 1e-12)
        normalized_chunk = raw_chunk / max(l1, 1e-12)
        hard_chunk = np.mean(
            (matrix_chunk > 0) == observed_signs,
            axis=1
        )
        registered_end = min(end, registered_count)
        if start < registered_count:
            take = registered_end - start
            registered_z[start:registered_end] = z_chunk[:take]
            registered_normalized[start:registered_end] = normalized_chunk[:take]
            registered_hard[start:registered_end] = hard_chunk[:take]
        null_start = max(0, registered_count - start)
        if null_start < len(z_chunk):
            null_best_z = max(
                null_best_z,
                float(np.max(z_chunk[null_start:]))
            )

    registered_scores = []
    if include_registered_records:
        raw_scale = max(l2, 1e-12)
        for index, trace_id in enumerate(prepared["trace_ids"]):
            registered_scores.append({
                "trace_id": trace_id,
                "raw_score": float(registered_z[index] * raw_scale),
                "normalized_score": float(registered_normalized[index]),
                "z_score": float(registered_z[index]),
                "hard_match_rate": float(registered_hard[index])
            })

    return {
        "observed_bits": observed_count,
        "registered_scores": registered_scores,
        "registered_z_scores": registered_z,
        "registered_normalized_scores": registered_normalized,
        "registered_hard_match_rates": registered_hard,
        "null_best_z": null_best_z,
        "normalizer_l1": l1,
        "normalizer_l2": l2
    }


def decide_trace_attribution(
        best_by_trace,
        null_best_z,
        bit_count=252,
        min_observed_ratio=0.50,
        min_normalized_score=0.12,
        min_z_score=5.0,
        min_margin_z=1.5
):
    ranked = sorted(
        best_by_trace.values(),
        key=lambda item: (
            item["z_score"],
            item["normalized_score"],
            item["hard_match_rate"]
        ),
        reverse=True
    )
    thresholds = {
        "min_observed_bits": int(np.ceil(bit_count * min_observed_ratio)),
        "min_observed_ratio": float(min_observed_ratio),
        "min_normalized_score": float(min_normalized_score),
        "min_z_score": float(min_z_score),
        "min_margin_z": float(min_margin_z)
    }
    if not ranked:
        return {
            "accepted": False,
            "status": "REJECTED_EMPTY_REGISTRY",
            "thresholds": thresholds,
            "selected": None,
            "top_registered": []
        }

    best = ranked[0]
    runner_up_z = ranked[1]["z_score"] if len(ranked) > 1 else -np.inf
    competitor_z = max(float(null_best_z), float(runner_up_z))
    margin_z = float(best["z_score"] - competitor_z)
    selected = {
        **best,
        "runner_up_registered_z": (
            float(runner_up_z) if np.isfinite(runner_up_z) else None
        ),
        "null_best_z": (
            float(null_best_z) if np.isfinite(null_best_z) else None
        ),
        "competitor_z": (
            float(competitor_z) if np.isfinite(competitor_z) else None
        ),
        "margin_z": margin_z
    }

    failures = []
    if int(best["observed_bits"]) < thresholds["min_observed_bits"]:
        failures.append("INSUFFICIENT_OBSERVED_BITS")
    if float(best["normalized_score"]) < min_normalized_score:
        failures.append("LOW_NORMALIZED_SCORE")
    if float(best["z_score"]) < min_z_score:
        failures.append("LOW_Z_SCORE")
    if margin_z < min_margin_z:
        failures.append("LOW_COMPETITOR_MARGIN")

    return {
        "accepted": not failures,
        "status": "REGISTRY_ML_ACCEPTED" if not failures else "REGISTRY_ML_REJECTED",
        "rejection_reasons": failures,
        "thresholds": thresholds,
        "selected": selected,
        "top_registered": ranked[:5]
    }
