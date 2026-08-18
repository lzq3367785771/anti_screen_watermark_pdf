import hashlib

import cv2
import numpy as np

from codec import (
    bits_to_bytes,
    hamming_soft_erasure_decode,
    verify_payload
)
from trace_registry import (
    decide_trace_attribution,
    prepare_trace_candidates,
    score_trace_candidates
)
from watermark import embed_watermark, extract_watermark_with_erasure


SYNC_PILOT_VERSION = "pn64-v1"


def generate_sync_pilot(key, bit_count=64):
    """生成确定、近似平衡且不携带TraceID的已知PN导频。"""

    if bit_count < 16:
        raise ValueError("同步导频至少需要16 bit")

    digest = hashlib.sha256(
        f"{key}:{SYNC_PILOT_VERSION}:{bit_count}".encode("utf-8")
    ).digest()
    seed = int.from_bytes(digest[:8], byteorder="big")
    rng = np.random.default_rng(seed)
    pilot = np.asarray(
        [0] * (bit_count // 2) + [1] * (bit_count - bit_count // 2),
        dtype=np.uint8
    )
    rng.shuffle(pilot)
    return pilot.astype(int).tolist()


def embed_sync_pilot(
        image,
        key,
        payload_bit_count,
        payload_repeat,
        block_size,
        pilot_bit_count=64,
        pilot_repeat=6,
        pilot_alpha=80.0
):
    """在载荷位置之后嵌入独立导频，保证两组DCT块不重叠。"""

    pilot_bits = generate_sync_pilot(key, pilot_bit_count)
    position_offset = int(payload_bit_count) * int(payload_repeat)
    result = embed_watermark(
        image,
        pilot_bits,
        key,
        alpha=float(pilot_alpha),
        repeat=int(pilot_repeat),
        block_size=int(block_size),
        position_offset=position_offset
    )
    config = {
        "enabled": True,
        "version": SYNC_PILOT_VERSION,
        "bit_count": int(pilot_bit_count),
        "repeat": int(pilot_repeat),
        "alpha": float(pilot_alpha),
        "position_offset": position_offset
    }
    return result, config


def _warp_candidate(image, valid_mask, scale, dx, dy):
    height, width = image.shape[:2]
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    matrix = np.float32([
        [scale, 0.0, dx + (1.0 - scale) * center_x],
        [0.0, scale, dy + (1.0 - scale) * center_y]
    ])
    warped = cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT
    )
    warped_mask = cv2.warpAffine(
        valid_mask,
        matrix,
        (width, height),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0
    )
    return warped, warped_mask, matrix


def _pilot_measure(
        image,
        valid_mask,
        key,
        block_size,
        sync_config
):
    expected = generate_sync_pilot(key, int(sync_config["bit_count"]))
    bits, scores, details = extract_watermark_with_erasure(
        image,
        valid_mask,
        bit_count=len(expected),
        key=key,
        repeat=int(sync_config["repeat"]),
        block_size=int(block_size),
        min_valid_repeats=2,
        position_offset=int(sync_config["position_offset"])
    )
    observed = [
        (expected_bit, actual_bit, score)
        for expected_bit, actual_bit, score in zip(expected, bits, scores)
        if actual_bit is not None and score is not None
    ]

    if not observed:
        return {
            "observed_bits": 0,
            "match_rate": 0.0,
            "normalized_correlation": -1.0,
            "mean_signed_correlation": 0.0,
            "valid_dct_unit_ratio": details["valid_dct_unit_ratio"]
        }

    signed = np.asarray([
        (1.0 if expected_bit else -1.0) * float(score)
        for expected_bit, _, score in observed
    ])
    absolute_sum = float(np.sum(np.abs(signed)))
    normalized = (
        float(np.sum(signed) / absolute_sum)
        if absolute_sum > 1e-12
        else -1.0
    )
    matches = sum(expected_bit == actual_bit for expected_bit, actual_bit, _ in observed)
    return {
        "observed_bits": len(observed),
        "match_rate": float(matches / len(observed)),
        "normalized_correlation": normalized,
        "mean_signed_correlation": float(np.mean(signed)),
        "valid_dct_unit_ratio": details["valid_dct_unit_ratio"]
    }


def _candidate_rank(record):
    transform_cost = (
        abs(float(record["scale"]) - 1.0) * 1000.0
        + float(record["dx"]) * float(record["dx"])
        + float(record["dy"]) * float(record["dy"])
    )
    return (
        round(record["normalized_correlation"], 6),
        round(record["match_rate"], 6),
        round(record["mean_signed_correlation"], 6),
        record["observed_bits"],
        -transform_cost
    )


def refine_with_sync_pilot(
        image,
        valid_mask,
        key,
        block_size,
        sync_config,
        translation_radius=None,
        scales=None,
        min_match_rate=0.62,
        min_normalized_correlation=0.08,
        fine_scale_radius=0.004,
        fine_scale_step=0.001,
        fine_translation_radius=1,
        fine_seed_count=2,
        top_k=12
):
    """用PN导频执行粗尺度/平移搜索，再围绕最优候选精搜索。"""

    if image is None or valid_mask is None:
        raise ValueError("同步精校需要内容图和有效区域Mask")
    if not sync_config or not sync_config.get("enabled", False):
        return image, valid_mask, {
            "enabled": False,
            "status": "NO_SYNC_PILOT"
        }

    if translation_radius is None:
        translation_radius = max(2, min(4, int(block_size) // 2))
    custom_scales = scales is not None
    if scales is None:
        scales = np.arange(0.985, 1.015 + 0.0001, 0.005).tolist()
    if top_k < 1:
        raise ValueError("top_k必须至少为1")

    candidate_map = {}

    def evaluate(scale, dx, dy, stage):
        scale = round(float(scale), 6)
        dx = float(dx)
        dy = float(dy)
        key_tuple = (scale, dx, dy)

        if key_tuple in candidate_map:
            existing = candidate_map[key_tuple]
            if stage not in existing["search_stages"]:
                existing["search_stages"].append(stage)
            return existing

        candidate_image, candidate_mask, matrix = _warp_candidate(
            image,
            valid_mask,
            scale,
            dx,
            dy
        )
        measure = _pilot_measure(
            candidate_image,
            candidate_mask,
            key,
            block_size,
            sync_config
        )
        record = {
            "scale": scale,
            "dx": dx,
            "dy": dy,
            "matrix": matrix.tolist(),
            "search_stages": [stage],
            **measure
        }
        candidate_map[key_tuple] = record
        return record

    for scale in scales:
        for dy in range(-int(translation_radius), int(translation_radius) + 1):
            for dx in range(-int(translation_radius), int(translation_radius) + 1):
                evaluate(scale, dx, dy, "coarse")

    coarse_candidates = sorted(
        candidate_map.values(),
        key=_candidate_rank,
        reverse=True
    )

    if not custom_scales and fine_scale_step > 0 and fine_seed_count > 0:
        fine_offsets = np.arange(
            -float(fine_scale_radius),
            float(fine_scale_radius) + float(fine_scale_step) / 2.0,
            float(fine_scale_step)
        )
        for seed in coarse_candidates[:int(fine_seed_count)]:
            for scale_offset in fine_offsets:
                fine_scale = float(seed["scale"]) + float(scale_offset)
                for dy_offset in range(
                    -int(fine_translation_radius),
                    int(fine_translation_radius) + 1
                ):
                    for dx_offset in range(
                        -int(fine_translation_radius),
                        int(fine_translation_radius) + 1
                    ):
                        evaluate(
                            fine_scale,
                            seed["dx"] + dx_offset,
                            seed["dy"] + dy_offset,
                            "fine"
                        )

    candidates = sorted(
        candidate_map.values(),
        key=_candidate_rank,
        reverse=True
    )
    best_record = candidates[0]
    best_image, best_mask, _ = _warp_candidate(
        image,
        valid_mask,
        best_record["scale"],
        best_record["dx"],
        best_record["dy"]
    )
    required_observed = max(16, int(0.75 * int(sync_config["bit_count"])))
    accepted = bool(
        best_record["observed_bits"] >= required_observed
        and best_record["match_rate"] >= float(min_match_rate)
        and best_record["normalized_correlation"] >= float(
            min_normalized_correlation
        )
    )
    identity = candidate_map.get((1.0, 0.0, 0.0))
    evaluated_scales = sorted({record["scale"] for record in candidates})
    search_boundary = bool(
        best_record["scale"] == evaluated_scales[0]
        or best_record["scale"] == evaluated_scales[-1]
    )
    diagnostics = {
        "enabled": True,
        "status": "ACCEPTED" if accepted else "REJECTED_LOW_PILOT_CONFIDENCE",
        "pilot_version": sync_config.get("version", SYNC_PILOT_VERSION),
        "search_version": "v1.9.1-coarse-fine",
        "candidate_count": len(candidates),
        "coarse_candidate_count": len(coarse_candidates),
        "translation_radius": int(translation_radius),
        "coarse_scales": [float(value) for value in scales],
        "fine_scale_radius": float(fine_scale_radius),
        "fine_scale_step": float(fine_scale_step),
        "fine_translation_radius": int(fine_translation_radius),
        "fine_seed_count": int(fine_seed_count),
        "search_boundary": search_boundary,
        "thresholds": {
            "min_match_rate": float(min_match_rate),
            "min_normalized_correlation": float(min_normalized_correlation),
            "min_observed_bits": required_observed
        },
        "identity": identity,
        "best": best_record,
        "top_candidates": candidates[:int(top_k)]
    }

    if not accepted:
        return image, valid_mask, diagnostics

    return best_image, best_mask, diagnostics


def rerank_sync_candidates_with_crc(
        image,
        valid_mask,
        key,
        block_size,
        payload_config,
        synchronization,
        top_k=12
):
    """解码导频Top-K候选；CRC通过时覆盖纯导频排名。"""

    candidates = synchronization.get("top_candidates", [])[:int(top_k)]
    if (
        not synchronization.get("enabled", False)
        or not synchronization.get("status", "").startswith("ACCEPTED")
        or not candidates
    ):
        return image, valid_mask, {
            "enabled": False,
            "status": "SKIPPED",
            "reason": synchronization.get("status", "NO_CANDIDATES")
        }

    records = []

    for pilot_rank, candidate in enumerate(candidates, start=1):
        candidate_image, candidate_mask, _ = _warp_candidate(
            image,
            valid_mask,
            candidate["scale"],
            candidate["dx"],
            candidate["dy"]
        )
        _, soft_scores, details = extract_watermark_with_erasure(
            candidate_image,
            candidate_mask,
            bit_count=int(payload_config["bit_count"]),
            key=key,
            repeat=int(payload_config["repeat"]),
            block_size=int(block_size)
        )
        decoded_bits, hamming_records = hamming_soft_erasure_decode(soft_scores)
        unresolved = any(bit is None for bit in decoded_bits)
        crc_ok = False
        recovered_trace_id = None

        if not unresolved:
            recovered_payload = bits_to_bytes(decoded_bits)
            crc_ok, recovered_trace_id, _, _ = verify_payload(recovered_payload)

        margins = [
            float(record["margin"])
            for record in hamming_records
            if record.get("margin") is not None
        ]
        valid_scores = [abs(float(score)) for score in soft_scores if score is not None]
        records.append({
            "pilot_rank": pilot_rank,
            "scale": float(candidate["scale"]),
            "dx": float(candidate["dx"]),
            "dy": float(candidate["dy"]),
            "pilot_match_rate": float(candidate["match_rate"]),
            "pilot_normalized_correlation": float(
                candidate["normalized_correlation"]
            ),
            "observed_payload_bits": int(
                len(soft_scores) - details["erasure_count"]
            ),
            "payload_confidence_p50": (
                float(np.median(valid_scores)) if valid_scores else 0.0
            ),
            "mean_hamming_margin": (
                float(np.mean(margins)) if margins else 0.0
            ),
            "unresolved_payload": bool(unresolved),
            "crc_ok": bool(crc_ok),
            "recovered_trace_id": recovered_trace_id
        })

    crc_records = [record for record in records if record["crc_ok"]]
    if crc_records:
        selected = crc_records[0]
        selection_status = "CRC_SELECTED"
    else:
        selected = max(
            records,
            key=lambda record: (
                record["mean_hamming_margin"],
                record["pilot_normalized_correlation"],
                -record["pilot_rank"]
            )
        )
        selection_status = (
            "PILOT_SELECTED_NO_CRC"
            if selected["pilot_rank"] == 1
            else "PAYLOAD_LIKELIHOOD_SELECTED_NO_CRC"
        )
    selected_image, selected_mask, matrix = _warp_candidate(
        image,
        valid_mask,
        selected["scale"],
        selected["dx"],
        selected["dy"]
    )
    selected = {
        **selected,
        "matrix": matrix.tolist()
    }
    diagnostics = {
        "enabled": True,
        "status": selection_status,
        "selection_policy": (
            "CRC first; otherwise maximum mean Hamming soft margin, "
            "then pilot correlation"
        ),
        "top_k_requested": int(top_k),
        "candidate_count": len(records),
        "crc_pass_count": len(crc_records),
        "selected": selected,
        "candidates": records
    }
    return selected_image, selected_mask, diagnostics


def rerank_sync_candidates_with_registry(
        image,
        valid_mask,
        key,
        block_size,
        payload_config,
        synchronization,
        registry,
        top_k=12,
        null_candidate_count=4096,
        prepared_candidates=None,
        cache_dir=None,
        score_chunk_size=16384
):
    """在导频Top-K几何候选上执行注册TraceID软最大似然检索。"""

    candidates = synchronization.get("top_candidates", [])[:int(top_k)]
    if (
        not synchronization.get("enabled", False)
        or not synchronization.get("status", "").startswith("ACCEPTED")
        or not candidates
    ):
        return image, valid_mask, {
            "enabled": False,
            "accepted": False,
            "status": "SKIPPED_NO_SYNC_CANDIDATES"
        }
    if not registry or not registry.get("records"):
        return image, valid_mask, {
            "enabled": False,
            "accepted": False,
            "status": "SKIPPED_EMPTY_REGISTRY"
        }

    prepared = prepared_candidates or prepare_trace_candidates(
        registry,
        null_candidate_count=null_candidate_count,
        cache_dir=cache_dir
    )
    registered_count = int(prepared["registered_count"])
    best_z = np.full(registered_count, -np.inf, dtype=np.float32)
    best_normalized = np.full(registered_count, -np.inf, dtype=np.float32)
    best_hard = np.zeros(registered_count, dtype=np.float32)
    best_raw = np.full(registered_count, -np.inf, dtype=np.float32)
    best_observed = np.zeros(registered_count, dtype=np.int16)
    best_transform_index = np.full(registered_count, -1, dtype=np.int16)
    null_best_z = -np.inf
    transform_records = []
    transform_metadata = []

    for pilot_rank, candidate in enumerate(candidates, start=1):
        candidate_image, candidate_mask, matrix = _warp_candidate(
            image,
            valid_mask,
            candidate["scale"],
            candidate["dx"],
            candidate["dy"]
        )
        _, soft_scores, details = extract_watermark_with_erasure(
            candidate_image,
            candidate_mask,
            bit_count=int(payload_config["bit_count"]),
            key=key,
            repeat=int(payload_config["repeat"]),
            block_size=int(block_size)
        )
        scored = score_trace_candidates(
            soft_scores,
            prepared,
            chunk_size=score_chunk_size,
            include_registered_records=False
        )
        null_best_z = max(null_best_z, float(scored["null_best_z"]))
        z_scores = scored["registered_z_scores"]
        normalized_scores = scored["registered_normalized_scores"]
        hard_rates = scored["registered_hard_match_rates"]
        update = (z_scores > best_z) | (
            (z_scores == best_z) & (normalized_scores > best_normalized)
        )
        transform_index = len(transform_metadata)
        best_z[update] = z_scores[update]
        best_normalized[update] = normalized_scores[update]
        best_hard[update] = hard_rates[update]
        best_raw[update] = (
            z_scores[update] * float(scored["normalizer_l2"])
        )
        best_observed[update] = int(scored["observed_bits"])
        best_transform_index[update] = transform_index
        transform_metadata.append({
            "pilot_rank": int(pilot_rank),
            "scale": float(candidate["scale"]),
            "dx": float(candidate["dx"]),
            "dy": float(candidate["dy"]),
            "matrix": matrix.tolist(),
            "pilot_match_rate": float(candidate["match_rate"]),
            "pilot_normalized_correlation": float(
                candidate["normalized_correlation"]
            )
        })
        transform_best_index = int(np.argmax(z_scores))
        transform_records.append({
            "pilot_rank": int(pilot_rank),
            "scale": float(candidate["scale"]),
            "dx": float(candidate["dx"]),
            "dy": float(candidate["dy"]),
            "observed_bits": int(scored["observed_bits"]),
            "best_trace_id": prepared["trace_ids"][transform_best_index],
            "best_z_score": float(z_scores[transform_best_index]),
            "best_normalized_score": float(
                normalized_scores[transform_best_index]
            ),
            "null_best_z": float(scored["null_best_z"])
        })

    top_count = min(5, registered_count)
    top_indices = np.argsort(best_z)[-top_count:][::-1]
    best_by_trace = {}
    for index in top_indices:
        metadata = transform_metadata[int(best_transform_index[index])]
        trace_id = prepared["trace_ids"][int(index)]
        best_by_trace[trace_id] = {
            "trace_id": trace_id,
            "raw_score": float(best_raw[index]),
            "normalized_score": float(best_normalized[index]),
            "z_score": float(best_z[index]),
            "hard_match_rate": float(best_hard[index]),
            "observed_bits": int(best_observed[index]),
            **metadata
        }

    decision = decide_trace_attribution(
        best_by_trace,
        null_best_z,
        bit_count=int(payload_config["bit_count"])
    )
    selected = decision.get("selected")
    if selected is None:
        return image, valid_mask, {
            "enabled": True,
            **decision,
            "registry_count": int(prepared["registered_count"]),
            "null_candidate_count": int(prepared["null_candidate_count"]),
            "codeword_cache_path": prepared.get("cache_path"),
            "codeword_cache_hit": bool(prepared.get("cache_hit", False)),
            "transforms_tested": len(transform_records),
            "transform_candidates": transform_records
        }

    selected_image, selected_mask, _ = _warp_candidate(
        image,
        valid_mask,
        selected["scale"],
        selected["dx"],
        selected["dy"]
    )
    diagnostics = {
        "enabled": True,
        **decision,
        "selection_policy": (
            "maximum soft-codeword z score across PN Top-K; reject unless "
            "absolute score and distinct/null competitor margin pass"
        ),
        "registry_count": int(prepared["registered_count"]),
        "null_candidate_count": int(prepared["null_candidate_count"]),
        "codeword_cache_path": prepared.get("cache_path"),
        "codeword_cache_hit": bool(prepared.get("cache_hit", False)),
        "score_chunk_size": int(score_chunk_size),
        "transforms_tested": len(transform_records),
        "transform_candidates": transform_records
    }
    return selected_image, selected_mask, diagnostics
