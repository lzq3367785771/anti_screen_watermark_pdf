import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from codec import (
    bits_to_bytes,
    build_payload,
    bytes_to_bits,
    generate_trace_id,
    hamming_encode,
    hamming_soft_erasure_decode,
    verify_payload
)
from native_screen import (
    choose_content_size,
    create_native_screen_board,
    default_marker_size,
    get_primary_screen_size,
    rectify_native_screen,
    show_board_fullscreen
)
from open_set_evaluation import (
    benchmark_command,
    dataset_init_command,
    probe_command,
    registry_stress_command
)
from synchronization import (
    embed_sync_pilot,
    refine_with_sync_pilot,
    rerank_sync_candidates_with_crc,
    rerank_sync_candidates_with_registry
)
from trace_registry import (
    load_trace_registry,
    register_trace_id,
    sync_trace_registry
)
from watermark import embed_watermark, extract_watermark_with_erasure


DEFAULT_KEY = "ANTI_SCREEN_SECRET_KEY_2026"
DEFAULT_TRACE_REGISTRY = "trace_registry.json"
BIT_COUNT = 252

V19_CARRIER_DEFAULTS = {
    8: {
        "alpha": 60.0,
        "repeat": 24,
        "pilot_alpha": 80.0,
        "pilot_repeat": 6
    },
    16: {
        "alpha": 80.0,
        "repeat": 8,
        "pilot_alpha": 100.0,
        "pilot_repeat": 6
    }
}


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def sha256_file(path):
    digest = hashlib.sha256()

    with Path(path).open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

    return digest.hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    with temporary.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)

    temporary.replace(path)


def read_json(path):
    with Path(path).open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def variant_directory(experiment_dir, block_size):
    return Path(experiment_dir) / f"variant_{int(block_size)}x{int(block_size)}"


def resolve_carrier_settings(args, block_size):
    block_size = int(block_size)

    if args.profile == "v19-sync":
        defaults = V19_CARRIER_DEFAULTS.get(block_size)
        if defaults is None:
            raise ValueError("v19-sync当前只支持8x8和16x16")
    else:
        defaults = {
            "alpha": 30.0,
            "repeat": 16,
            "pilot_alpha": None,
            "pilot_repeat": None
        }

    return {
        "alpha": float(args.alpha) if args.alpha is not None else defaults["alpha"],
        "repeat": int(args.repeat) if args.repeat is not None else defaults["repeat"],
        "pilot_alpha": defaults["pilot_alpha"],
        "pilot_repeat": defaults["pilot_repeat"]
    }


def resolve_screen_size(args):
    if args.screen_width is None and args.screen_height is None:
        return get_primary_screen_size()

    if args.screen_width is None or args.screen_height is None:
        raise ValueError("--screen-width 与 --screen-height 必须同时提供")

    return int(args.screen_width), int(args.screen_height)


def prepare_experiment(args):
    source_path = Path(args.source).resolve()
    source_image = cv2.imread(str(source_path))

    if source_image is None:
        raise FileNotFoundError(f"无法读取原图: {source_path}")

    screen_width, screen_height = resolve_screen_size(args)
    marker_size = (
        int(args.marker_size)
        if args.marker_size is not None
        else default_marker_size(screen_width, screen_height)
    )
    edge_pad = (
        int(args.edge_pad)
        if args.edge_pad is not None
        else max(8, marker_size // 8)
    )

    source_height, source_width = source_image.shape[:2]
    content_width, content_height = choose_content_size(
        source_width,
        source_height,
        screen_width,
        screen_height,
        marker_size,
        edge_pad,
        align=max(args.block_sizes)
    )

    interpolation = (
        cv2.INTER_AREA
        if content_width < source_width or content_height < source_height
        else cv2.INTER_CUBIC
    )
    final_source = cv2.resize(
        source_image,
        (content_width, content_height),
        interpolation=interpolation
    )

    trace_id = generate_trace_id()
    encoded_bits = hamming_encode(bytes_to_bits(build_payload(trace_id)))

    if len(encoded_bits) != BIT_COUNT:
        raise RuntimeError(f"编码长度异常: {len(encoded_bits)}")

    experiment_id = args.experiment_id or timestamp_id()
    experiment_dir = Path(args.output_root).resolve() / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=False)

    cv2.imwrite(str(experiment_dir / "final_source.png"), final_source)
    shutil.copy2(source_path, experiment_dir / f"source{source_path.suffix.lower()}")

    encoded_string = "".join(str(bit) for bit in encoded_bits)
    ground_truth = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "created_at": now_iso(),
        "trace_id": trace_id,
        "encoded_bit_count": len(encoded_bits),
        "encoded_bits": encoded_string,
        "payload": {
            "trace_bits": 128,
            "crc_bits": 16,
            "codec": "Hamming(7,4)",
            "encoded_bits": BIT_COUNT
        },
        "key_id": args.key_id,
        "key_sha256": hashlib.sha256(args.key.encode("utf-8")).hexdigest(),
        "source_sha256": sha256_file(source_path),
        "screen": {
            "width": screen_width,
            "height": screen_height,
            "expected_scale_percent": 100,
            "display_mode": "native_fullscreen_1_to_1"
        },
        "content": {
            "width": content_width,
            "height": content_height,
            "resize_before_embedding": True
        }
    }
    write_json(experiment_dir / "ground_truth.json", ground_truth)
    (experiment_dir / "trace_id.txt").write_text(trace_id + "\n", encoding="utf-8")
    (experiment_dir / "encoded_bits.txt").write_text(encoded_string + "\n", encoding="utf-8")

    variants = []

    for block_size in args.block_sizes:
        block_size = int(block_size)
        carrier = resolve_carrier_settings(args, block_size)
        variant_dir = variant_directory(experiment_dir, block_size)
        variant_dir.mkdir(parents=True, exist_ok=False)
        (variant_dir / "captures").mkdir()

        watermarked = embed_watermark(
            final_source,
            encoded_bits,
            args.key,
            alpha=carrier["alpha"],
            repeat=carrier["repeat"],
            block_size=block_size
        )
        if args.profile == "v19-sync":
            watermarked, sync_pilot = embed_sync_pilot(
                watermarked,
                args.key,
                payload_bit_count=BIT_COUNT,
                payload_repeat=carrier["repeat"],
                block_size=block_size,
                pilot_bit_count=64,
                pilot_repeat=carrier["pilot_repeat"],
                pilot_alpha=carrier["pilot_alpha"]
            )
        else:
            sync_pilot = {
                "enabled": False
            }
        board, layout = create_native_screen_board(
            watermarked,
            screen_width,
            screen_height,
            marker_size=marker_size,
            edge_pad=edge_pad
        )

        watermarked_path = variant_dir / "watermarked.png"
        board_path = variant_dir / "board_native.png"
        cv2.imwrite(str(watermarked_path), watermarked)
        cv2.imwrite(str(board_path), board)

        variant_manifest = {
            "schema_version": 2,
            "experiment_id": experiment_id,
            "variant": f"{block_size}x{block_size}",
            "created_at": now_iso(),
            "watermark": {
                "block_size": block_size,
                "profile": args.profile,
                "alpha": float(carrier["alpha"]),
                "repeat": int(carrier["repeat"]),
                "bit_count": BIT_COUNT,
                "channel": "Y",
                "coefficients": [[2, 3], [3, 2]],
                "sync_pilot": sync_pilot
            },
            "layout": layout,
            "files": {
                "watermarked": watermarked_path.name,
                "board": board_path.name
            },
            "sha256": {
                "watermarked": sha256_file(watermarked_path),
                "board": sha256_file(board_path)
            },
            "captures": []
        }
        write_json(variant_dir / "manifest.json", variant_manifest)
        (variant_dir / "trace_id.txt").write_text(trace_id + "\n", encoding="utf-8")
        (variant_dir / "encoded_bits.txt").write_text(encoded_string + "\n", encoding="utf-8")
        variants.append(str(variant_dir.name))

    group_manifest = {
        "schema_version": 2,
        "experiment_id": experiment_id,
        "created_at": now_iso(),
        "variants": variants,
        "carrier_profile": args.profile,
        "alpha_override": float(args.alpha) if args.alpha is not None else None,
        "repeat_override": int(args.repeat) if args.repeat is not None else None,
        "instructions": [
            "使用display命令全屏展示，每次只展示一个variant",
            "手机拍照后使用import命令导入，禁止重新运行prepare覆盖真值",
            "8x8和16x16使用相同手机、距离、角度与相机设置"
        ]
    }
    write_json(experiment_dir / "experiment.json", group_manifest)

    registry_path = Path(args.trace_registry).resolve()
    registry = register_trace_id(
        registry_path,
        trace_id,
        {
            "experiment_id": experiment_id,
            "created_at": ground_truth["created_at"],
            "key_id": args.key_id,
            "registry_source": str((experiment_dir / "ground_truth.json").resolve())
        }
    )

    print("实验已创建:", experiment_dir)
    print("TraceID:", trace_id)
    print("Encoded bits:", len(encoded_bits))
    print("Native screen:", f"{screen_width}x{screen_height}")
    print("Final content:", f"{content_width}x{content_height}")
    print("Carrier profile:", args.profile)
    print("Variants:", ", ".join(variants))
    print("Trace registry:", registry_path, f"({len(registry['records'])} records)")
    return experiment_dir


def display_variant(args):
    experiment_dir = Path(args.experiment).resolve()
    variant_dir = variant_directory(experiment_dir, args.block_size)
    manifest = read_json(variant_dir / "manifest.json")
    board_path = variant_dir / manifest["files"]["board"]
    board = cv2.imread(str(board_path))

    if board is None:
        raise FileNotFoundError(f"无法读取实验画布: {board_path}")

    actual_hash = sha256_file(board_path)
    if actual_hash != manifest["sha256"]["board"]:
        raise RuntimeError("实验画布哈希不一致，文件可能已被修改")

    print(f"正在1:1全屏展示 {manifest['variant']}，按Esc或Q退出")
    print("请保持浏览器/系统缩放不参与本次显示，并锁定手机曝光和对焦")
    show_board_fullscreen(board, f"Watermark {manifest['variant']}")


def import_capture(args):
    experiment_dir = Path(args.experiment).resolve()
    variant_dir = variant_directory(experiment_dir, args.block_size)
    variant_manifest_path = variant_dir / "manifest.json"
    variant_manifest = read_json(variant_manifest_path)
    ground_truth = read_json(experiment_dir / "ground_truth.json")
    photo_path = Path(args.photo).resolve()

    if not photo_path.is_file():
        raise FileNotFoundError(f"找不到手机照片: {photo_path}")

    if cv2.imread(str(photo_path)) is None:
        raise ValueError(f"无法读取手机照片: {photo_path}")

    capture_id = args.capture_id or timestamp_id()
    capture_dir = variant_dir / "captures" / capture_id
    capture_dir.mkdir(parents=True, exist_ok=False)
    suffix = photo_path.suffix.lower() or ".jpg"
    stored_photo = capture_dir / f"phone{suffix}"
    shutil.copy2(photo_path, stored_photo)

    capture_manifest = {
        "schema_version": 1,
        "capture_id": capture_id,
        "captured_or_imported_at": now_iso(),
        "experiment_id": ground_truth["experiment_id"],
        "variant": variant_manifest["variant"],
        "trace_id": ground_truth["trace_id"],
        "encoded_bit_count": ground_truth["encoded_bit_count"],
        "encoded_bits": ground_truth["encoded_bits"],
        "photo": stored_photo.name,
        "photo_sha256": sha256_file(stored_photo),
        "capture_conditions": {
            "device": args.device,
            "distance_cm": args.distance_cm,
            "angle_deg": args.angle_deg,
            "notes": args.notes
        }
    }
    write_json(capture_dir / "capture_manifest.json", capture_manifest)

    variant_manifest["captures"].append({
        "capture_id": capture_id,
        "path": str(Path("captures") / capture_id),
        "imported_at": capture_manifest["captured_or_imported_at"]
    })
    write_json(variant_manifest_path, variant_manifest)

    print("实拍已登记:", capture_dir)
    print("TraceID和252 bit已写入capture_manifest.json")

    if not args.no_evaluate:
        evaluate_capture(capture_dir, args.key, args.trace_registry)

    return capture_dir


def evaluate_capture(
        capture_dir,
        key=DEFAULT_KEY,
        trace_registry_path=DEFAULT_TRACE_REGISTRY
):
    capture_dir = Path(capture_dir).resolve()
    variant_dir = capture_dir.parent.parent
    experiment_dir = variant_dir.parent
    capture_manifest = read_json(capture_dir / "capture_manifest.json")
    variant_manifest = read_json(variant_dir / "manifest.json")
    ground_truth = read_json(experiment_dir / "ground_truth.json")
    photo_path = capture_dir / capture_manifest["photo"]
    trace_registry_path = Path(trace_registry_path).resolve()
    trace_registry = load_trace_registry(trace_registry_path)
    registered_trace_ids = {
        trace_id
        for trace_id, record in trace_registry.get("records", {}).items()
        if record.get("status", "issued") == "issued"
    }

    if sha256_file(photo_path) != capture_manifest["photo_sha256"]:
        raise RuntimeError("手机照片哈希不一致，拒绝使用失配真值评估")

    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    if key_hash != ground_truth["key_sha256"]:
        raise ValueError("提取密钥与实验清单不匹配")

    observed = cv2.imread(str(photo_path))
    if observed is None:
        raise ValueError(f"无法读取手机照片: {photo_path}")

    restored, rectified_board, valid_mask, geometry = rectify_native_screen(
        observed,
        variant_manifest["layout"]
    )

    watermark_config = variant_manifest["watermark"]
    sync_config = watermark_config.get("sync_pilot", {"enabled": False})
    synchronized, synchronized_mask, synchronization = refine_with_sync_pilot(
        restored,
        valid_mask,
        key,
        watermark_config["block_size"],
        sync_config
    )
    if synchronization.get("enabled", False):
        pilot_best = synchronization.get("best")
        (
            synchronized,
            synchronized_mask,
            crc_rerank
        ) = rerank_sync_candidates_with_crc(
            restored,
            valid_mask,
            key,
            watermark_config["block_size"],
            watermark_config,
            synchronization
        )
        synchronization["pilot_best"] = pilot_best
        synchronization["crc_rerank"] = crc_rerank

        if crc_rerank.get("enabled", False):
            selected = crc_rerank["selected"]
            synchronization["best"] = {
                "scale": selected["scale"],
                "dx": selected["dx"],
                "dy": selected["dy"],
                "matrix": selected["matrix"],
                "match_rate": selected["pilot_match_rate"],
                "normalized_correlation": selected[
                    "pilot_normalized_correlation"
                ],
                "selected_by": crc_rerank["status"]
            }
            synchronization["status"] = (
                "ACCEPTED_CRC_RERANK"
                if crc_rerank["status"] == "CRC_SELECTED"
                else (
                    "ACCEPTED_TOPK_SOFT_RERANK"
                    if crc_rerank["status"]
                    == "PAYLOAD_LIKELIHOOD_SELECTED_NO_CRC"
                    else "ACCEPTED_PILOT_TOP1"
                )
            )

        if (
            crc_rerank.get("enabled", False)
            and crc_rerank.get("crc_pass_count", 0) == 0
        ):
            (
                registry_image,
                registry_mask,
                registry_rerank
            ) = rerank_sync_candidates_with_registry(
                restored,
                valid_mask,
                key,
                watermark_config["block_size"],
                watermark_config,
                synchronization,
                trace_registry
            )
        else:
            registry_image = synchronized
            registry_mask = synchronized_mask
            registry_rerank = {
                "enabled": False,
                "accepted": False,
                "status": (
                    "SKIPPED_CRC_ALREADY_VALID"
                    if crc_rerank.get("crc_pass_count", 0) > 0
                    else "SKIPPED_CRC_RERANK_DISABLED"
                )
            }

        registry_rerank["registry_path"] = str(trace_registry_path)
        synchronization["registry_rerank"] = registry_rerank
        if registry_rerank.get("accepted", False):
            synchronized = registry_image
            synchronized_mask = registry_mask
            selected = registry_rerank["selected"]
            synchronization["best"] = {
                "scale": selected["scale"],
                "dx": selected["dx"],
                "dy": selected["dy"],
                "matrix": selected["matrix"],
                "match_rate": selected["pilot_match_rate"],
                "normalized_correlation": selected[
                    "pilot_normalized_correlation"
                ],
                "selected_by": registry_rerank["status"]
            }
            synchronization["status"] = "ACCEPTED_REGISTRY_ML"
    extracted_bits, soft_scores, details = extract_watermark_with_erasure(
        synchronized,
        synchronized_mask,
        bit_count=watermark_config["bit_count"],
        key=key,
        repeat=watermark_config["repeat"],
        block_size=watermark_config["block_size"]
    )
    expected_bits = [int(bit) for bit in ground_truth["encoded_bits"]]
    observed_pairs = [
        (expected, actual)
        for expected, actual in zip(expected_bits, extracted_bits)
        if actual is not None
    ]
    bit_errors = sum(
        expected != actual
        for expected, actual in observed_pairs
    )
    observed_bit_count = len(observed_pairs)
    erasure_count = len(expected_bits) - observed_bit_count
    raw_ber = (
        bit_errors / observed_bit_count
        if observed_bit_count
        else 1.0
    )
    effective_failure_rate = (
        (bit_errors + erasure_count) / len(expected_bits)
    )

    decoded_bits, hamming_records = hamming_soft_erasure_decode(soft_scores)
    unresolved_blocks = [
        record
        for record in hamming_records
        if record["status"] in {
            "INSUFFICIENT_OBSERVATIONS",
            "AMBIGUOUS"
        }
    ]

    if unresolved_blocks or any(bit is None for bit in decoded_bits):
        crc_ok = False
        hamming_recovered_trace_id = None
        received_crc = None
        calculated_crc = None
    else:
        recovered_payload = bits_to_bytes(decoded_bits)
        (
            crc_ok,
            hamming_recovered_trace_id,
            received_crc,
            calculated_crc
        ) = verify_payload(recovered_payload)

    registry_rerank = synchronization.get("registry_rerank", {})
    if crc_ok and hamming_recovered_trace_id in registered_trace_ids:
        recovered_trace_id = hamming_recovered_trace_id
        attribution_method = "HAMMING_CRC_REGISTERED"
        attribution_accepted = True
    elif crc_ok:
        recovered_trace_id = hamming_recovered_trace_id
        attribution_method = "HAMMING_CRC_UNREGISTERED"
        attribution_accepted = False
    elif registry_rerank.get("accepted", False):
        recovered_trace_id = registry_rerank["selected"]["trace_id"]
        attribution_method = "REGISTRY_SOFT_ML"
        attribution_accepted = True
    else:
        recovered_trace_id = None
        attribution_method = None
        attribution_accepted = False

    trace_ok = bool(
        attribution_accepted
        and recovered_trace_id == ground_truth["trace_id"]
    )

    repeat_agreements = []
    for bit, scores in zip(extracted_bits, details["repeat_scores"]):
        valid_scores = [score for score in scores if score is not None]
        if bit is None or not valid_scores:
            continue
        signs = [1 if score > 0 else 0 for score in valid_scores]
        repeat_agreements.append(
            sum(sign == bit for sign in signs) / len(signs)
        )

    valid_confidences = [
        abs(score)
        for score in soft_scores
        if score is not None
    ]
    if valid_confidences:
        confidence_percentiles = np.percentile(
            valid_confidences,
            [0, 10, 25, 50, 75, 90, 100]
        ).tolist()
    else:
        confidence_percentiles = [0.0] * 7

    cv2.imwrite(str(capture_dir / "rectified_board.png"), rectified_board)
    cv2.imwrite(str(capture_dir / "restored_content.png"), restored)
    cv2.imwrite(str(capture_dir / "valid_content_mask.png"), valid_mask)
    if synchronization.get("enabled", False):
        cv2.imwrite(str(capture_dir / "synchronized_content.png"), synchronized)
        cv2.imwrite(
            str(capture_dir / "synchronized_valid_mask.png"),
            synchronized_mask
        )

    decoded_with_erasures = sum(
        record["status"] == "DECODED_WITH_ERASURES"
        for record in hamming_records
    )
    ambiguous_blocks = sum(
        record["status"] == "AMBIGUOUS"
        for record in hamming_records
    )
    insufficient_blocks = sum(
        record["status"] == "INSUFFICIENT_OBSERVATIONS"
        for record in hamming_records
    )

    report = {
        "schema_version": 4,
        "evaluated_at": now_iso(),
        "experiment_id": ground_truth["experiment_id"],
        "capture_id": capture_manifest["capture_id"],
        "variant": variant_manifest["variant"],
        "ground_truth": {
            "trace_id": ground_truth["trace_id"],
            "encoded_bits": len(expected_bits)
        },
        "geometry": geometry,
        "synchronization": synchronization,
        "watermark": {
            "block_size": watermark_config["block_size"],
            "alpha": watermark_config["alpha"],
            "repeat": watermark_config["repeat"],
            "observed_bit_count": int(observed_bit_count),
            "erasure_count": int(erasure_count),
            "erasure_rate": float(erasure_count / len(expected_bits)),
            "bit_errors": int(bit_errors),
            "raw_bit_accuracy_percent": float((1.0 - raw_ber) * 100.0),
            "raw_ber": float(raw_ber),
            "effective_failure_rate": float(effective_failure_rate),
            "valid_dct_unit_ratio": details["valid_dct_unit_ratio"],
            "valid_repeat_counts": details["valid_repeat_counts"],
            "erasure_indices": details["erasure_indices"],
            "confidence_percentiles": {
                name: float(value)
                for name, value in zip(
                    ["p0", "p10", "p25", "p50", "p75", "p90", "p100"],
                    confidence_percentiles
                )
            },
            "mean_repeat_sign_agreement": (
                float(np.mean(repeat_agreements))
                if repeat_agreements
                else 0.0
            )
        },
        "decode": {
            "decoder": "Hamming(7,4) soft codebook with erasures",
            "decoded_with_erasure_blocks": int(decoded_with_erasures),
            "ambiguous_blocks": int(ambiguous_blocks),
            "insufficient_observation_blocks": int(insufficient_blocks),
            "unresolved_blocks": [
                record["block"] for record in unresolved_blocks
            ],
            "block_records": hamming_records,
            "crc_ok": bool(crc_ok),
            "trace_ok": trace_ok,
            "recovered_trace_id": recovered_trace_id,
            "hamming_recovered_trace_id": hamming_recovered_trace_id,
            "received_crc": received_crc,
            "calculated_crc": calculated_crc,
            "attribution": {
                "accepted": bool(attribution_accepted),
                "method": attribution_method,
                "trace_id": recovered_trace_id,
                "ground_truth_match": trace_ok,
                "decision_is_blind": True,
                "ground_truth_used_only_for_metrics": True
            }
        }
    }
    write_json(capture_dir / "report.json", report)

    print()
    print("Capture:", capture_manifest["capture_id"])
    print("Variant:", variant_manifest["variant"])
    print(
        "Geometry:",
        f"grade {geometry['geometry_grade']}",
        f"({geometry['marker_count']} marker)"
    )
    print("Content coverage:", f"{geometry['content_coverage_ratio'] * 100:.2f}%")
    print("Synchronization:", synchronization["status"])
    if synchronization.get("best"):
        best_sync = synchronization["best"]
        print(
            "Pilot:",
            f"match={best_sync['match_rate'] * 100:.2f}%",
            f"corr={best_sync['normalized_correlation']:.3f}",
            f"scale={best_sync['scale']:.4f}",
            f"shift=({best_sync['dx']:.1f},{best_sync['dy']:.1f})"
        )
    crc_rerank = synchronization.get("crc_rerank")
    if crc_rerank and crc_rerank.get("enabled", False):
        print(
            "Top-K/CRC:",
            crc_rerank["status"],
            f"tested={crc_rerank['candidate_count']}",
            f"crc_pass={crc_rerank['crc_pass_count']}"
        )
    registry_rerank = synchronization.get("registry_rerank")
    if registry_rerank and registry_rerank.get("enabled", False):
        selected_registry = registry_rerank.get("selected")
        if selected_registry:
            print(
                "Registry ML:",
                registry_rerank["status"],
                f"records={registry_rerank['registry_count']}",
                f"z={selected_registry['z_score']:.3f}",
                f"margin={selected_registry['margin_z']:.3f}",
                f"match={selected_registry['hard_match_rate'] * 100:.2f}%"
            )
            print("Registry TraceID:", selected_registry["trace_id"])
    print("Observed bits:", f"{observed_bit_count}/{len(expected_bits)}")
    print("Erasures:", f"{erasure_count}/{len(expected_bits)}")
    print("Observed accuracy:", f"{(1.0 - raw_ber) * 100.0:.2f}%")
    print("Observed BER:", f"{raw_ber * 100.0:.2f}%")
    print("Confidence p50:", f"{confidence_percentiles[3]:.3f}")
    print("Reprojection mean:", f"{geometry['reprojection_mean_px']:.3f}px")
    print("CRC:", "PASS" if crc_ok else "FAIL")
    print(
        "Trace:",
        "SUCCESS" if trace_ok else "FAIL",
        f"({attribution_method or 'NO_ACCEPTED_ATTRIBUTION'})"
    )

    return report


def evaluate_all(args):
    experiment_dir = Path(args.experiment).resolve()

    for block_size in args.block_sizes:
        captures_dir = variant_directory(experiment_dir, block_size) / "captures"
        if not captures_dir.exists():
            continue

        for capture_dir in sorted(path for path in captures_dir.iterdir() if path.is_dir()):
            evaluate_capture(capture_dir, args.key, args.trace_registry)

    write_summary(experiment_dir)


def write_summary(experiment_dir):
    experiment_dir = Path(experiment_dir).resolve()
    rows = []

    for variant_dir in sorted(experiment_dir.glob("variant_*x*")):
        for report_path in sorted(variant_dir.glob("captures/*/report.json")):
            report = read_json(report_path)
            rows.append({
                "capture_id": report["capture_id"],
                "variant": report["variant"],
                "block_size": report["watermark"]["block_size"],
                "raw_accuracy_percent": report["watermark"]["raw_bit_accuracy_percent"],
                "raw_ber": report["watermark"]["raw_ber"],
                "erasure_rate": report["watermark"].get("erasure_rate", 0.0),
                "content_coverage_ratio": report["geometry"].get(
                    "content_coverage_ratio",
                    1.0
                ),
                "confidence_p50": report["watermark"]["confidence_percentiles"]["p50"],
                "reprojection_mean_px": report["geometry"]["reprojection_mean_px"],
                "crc_ok": report["decode"]["crc_ok"],
                "trace_ok": report["decode"]["trace_ok"],
                "attribution_method": report["decode"].get(
                    "attribution", {}
                ).get("method")
            })

    if not rows:
        print("尚无已评估实拍，无法生成A/B汇总")
        return None

    csv_path = experiment_dir / "ab_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    grouped = {}
    for row in rows:
        grouped.setdefault(row["variant"], []).append(row)

    aggregate = {}
    for variant, variant_rows in grouped.items():
        aggregate[variant] = {
            "captures": len(variant_rows),
            "mean_raw_accuracy_percent": float(np.mean([
                row["raw_accuracy_percent"] for row in variant_rows
            ])),
            "mean_raw_ber": float(np.mean([row["raw_ber"] for row in variant_rows])),
            "mean_erasure_rate": float(np.mean([
                row["erasure_rate"] for row in variant_rows
            ])),
            "mean_content_coverage_ratio": float(np.mean([
                row["content_coverage_ratio"] for row in variant_rows
            ])),
            "median_confidence_p50": float(np.median([
                row["confidence_p50"] for row in variant_rows
            ])),
            "trace_success_rate": float(np.mean([
                1.0 if row["trace_ok"] else 0.0 for row in variant_rows
            ]))
        }

    summary = {
        "generated_at": now_iso(),
        "experiment_id": read_json(experiment_dir / "ground_truth.json")["experiment_id"],
        "variants": aggregate,
        "capture_rows": rows
    }
    write_json(experiment_dir / "ab_summary.json", summary)

    print()
    print("A/B Summary")
    print("-" * 104)
    print(
        f"{'Variant':<10} {'N':>4} {'Accuracy':>10} {'BER':>10} "
        f"{'Erasure':>10} {'Coverage':>10} {'Conf p50':>12} {'Trace':>10}"
    )
    print("-" * 104)
    for variant, data in sorted(aggregate.items()):
        print(
            f"{variant:<10} "
            f"{data['captures']:>4} "
            f"{data['mean_raw_accuracy_percent']:>9.2f}% "
            f"{data['mean_raw_ber'] * 100:>9.2f}% "
            f"{data['mean_erasure_rate'] * 100:>9.2f}% "
            f"{data['mean_content_coverage_ratio'] * 100:>9.2f}% "
            f"{data['median_confidence_p50']:>12.3f} "
            f"{data['trace_success_rate'] * 100:>9.1f}%"
        )
    print()
    print("Saved:", csv_path)
    return summary


def summary_command(args):
    write_summary(args.experiment)


def registry_build_command(args):
    registry, stats = sync_trace_registry(
        Path(args.output).resolve(),
        experiments_roots=args.experiments_root,
        legacy_db_paths=args.legacy_db
    )
    print("Trace registry saved:", Path(args.output).resolve())
    print("Issued records:", len(registry["records"]))
    print("Experiment records seen:", stats["experiment_records_seen"])
    print("Legacy records seen:", stats["legacy_records_seen"])
    return registry


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "V1.9.3 开放集盲检、压力注册库与批量评估框架"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="生成配对真值和两套原生屏幕画布")
    prepare.add_argument("--source", required=True, help="原始内容图片")
    prepare.add_argument("--output-root", default="experiments")
    prepare.add_argument("--experiment-id")
    prepare.add_argument("--screen-width", type=int)
    prepare.add_argument("--screen-height", type=int)
    prepare.add_argument("--marker-size", type=int)
    prepare.add_argument("--edge-pad", type=int)
    prepare.add_argument("--block-sizes", type=int, nargs="+", default=[8, 16])
    prepare.add_argument(
        "--profile",
        choices=["legacy", "v19-sync"],
        default="legacy",
        help="legacy保持V1.8参数；v19-sync启用增强载体和PN同步导频"
    )
    prepare.add_argument("--alpha", type=float, help="覆盖profile的载荷强度")
    prepare.add_argument("--repeat", type=int, help="覆盖profile的载荷重复数")
    prepare.add_argument("--key", default=DEFAULT_KEY)
    prepare.add_argument("--key-id", default="default-2026")
    prepare.add_argument(
        "--trace-registry",
        default=DEFAULT_TRACE_REGISTRY,
        help="已发行TraceID注册表；prepare会自动登记新TraceID"
    )
    prepare.set_defaults(func=prepare_experiment)

    display = subparsers.add_parser("display", help="1:1全屏展示指定实验画布")
    display.add_argument("--experiment", required=True)
    display.add_argument("--block-size", type=int, choices=[8, 16], required=True)
    display.set_defaults(func=display_variant)

    import_parser = subparsers.add_parser("import", help="导入手机照片并绑定真值")
    import_parser.add_argument("--experiment", required=True)
    import_parser.add_argument("--block-size", type=int, choices=[8, 16], required=True)
    import_parser.add_argument("--photo", required=True)
    import_parser.add_argument("--capture-id")
    import_parser.add_argument("--device", default="")
    import_parser.add_argument("--distance-cm", type=float)
    import_parser.add_argument("--angle-deg", type=float, default=0.0)
    import_parser.add_argument("--notes", default="")
    import_parser.add_argument("--key", default=DEFAULT_KEY)
    import_parser.add_argument(
        "--trace-registry",
        default=DEFAULT_TRACE_REGISTRY
    )
    import_parser.add_argument("--no-evaluate", action="store_true")
    import_parser.set_defaults(func=import_capture)

    evaluate = subparsers.add_parser("evaluate-all", help="重新评估所有已登记实拍")
    evaluate.add_argument("--experiment", required=True)
    evaluate.add_argument("--block-sizes", type=int, nargs="+", default=[8, 16])
    evaluate.add_argument("--key", default=DEFAULT_KEY)
    evaluate.add_argument(
        "--trace-registry",
        default=DEFAULT_TRACE_REGISTRY
    )
    evaluate.set_defaults(func=evaluate_all)

    registry_build = subparsers.add_parser(
        "registry-build",
        help="从历史实验和旧trace_db构建已发行TraceID注册表"
    )
    registry_build.add_argument(
        "--experiments-root",
        nargs="+",
        default=["experiments"]
    )
    registry_build.add_argument(
        "--legacy-db",
        nargs="*",
        default=["trace_db.json"]
    )
    registry_build.add_argument(
        "--output",
        default=DEFAULT_TRACE_REGISTRY
    )
    registry_build.set_defaults(func=registry_build_command)

    registry_stress = subparsers.add_parser(
        "registry-stress",
        help="生成与正式注册表隔离的指定规模压力测试注册库"
    )
    registry_stress.add_argument("--base", default=DEFAULT_TRACE_REGISTRY)
    registry_stress.add_argument("--size", type=int, required=True)
    registry_stress.add_argument("--seed", default="v193-stress")
    registry_stress.add_argument("--output", required=True)
    registry_stress.set_defaults(func=registry_stress_command)

    dataset_init = subparsers.add_parser(
        "dataset-init",
        help="创建V1.9.3开放集样本CSV清单"
    )
    dataset_init.add_argument("--output", required=True)
    dataset_init.set_defaults(func=dataset_init_command)

    probe = subparsers.add_parser(
        "probe",
        help="不读取实验真值，对任意照片执行盲检和拒识"
    )
    probe.add_argument("--photo", required=True)
    probe.add_argument("--reference-experiment", required=True)
    probe.add_argument("--block-size", type=int, choices=[8, 16], required=True)
    probe.add_argument("--trace-registry", default=DEFAULT_TRACE_REGISTRY)
    probe.add_argument("--key", default=DEFAULT_KEY)
    probe.add_argument("--cache-dir")
    probe.add_argument("--output")
    probe.set_defaults(func=probe_command)

    benchmark = subparsers.add_parser(
        "benchmark-open-set",
        help="批量评估正样本、未知TraceID和其他负样本"
    )
    benchmark.add_argument("--dataset", required=True)
    benchmark.add_argument("--trace-registry", default=DEFAULT_TRACE_REGISTRY)
    benchmark.add_argument("--key", default=DEFAULT_KEY)
    benchmark.add_argument("--cache-dir")
    benchmark.add_argument("--output")
    benchmark.set_defaults(func=benchmark_command)

    summary = subparsers.add_parser("summary", help="生成8x8/16x16 A/B汇总")
    summary.add_argument("--experiment", required=True)
    summary.set_defaults(func=summary_command)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except Exception as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
