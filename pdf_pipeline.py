"""PDF V2.0A: page rendering, watermark issuance, extraction and photo tracing.

The production path deliberately does not depend on ArUco.  A photographed page
is aligned with the registered source page by local document features; a page
boundary detector is used as a fallback for low-feature full-page captures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from document_registry import (
    DOCUMENT_CODEWORD_BITS,
    DOCUMENT_WATERMARK_VERSION,
    attach_issue_artifact,
    decode_document_soft_scores,
    derive_page_key,
    encode_document_token,
    issue_document_trace,
    load_document_registry,
    register_document,
    score_registered_tokens,
    sha256_file,
)
from synchronization import (
    _pilot_measure,
    _warp_candidate,
    generate_sync_pilot,
    refine_with_sync_pilot,
)
from watermark import (
    embed_bit,
    extract_watermark_with_erasure,
    generate_positions,
)


DEFAULT_KEY = "ANTI_SCREEN_DOCUMENT_SECRET_KEY_2026"
MANIFEST_SCHEMA_VERSION = 1
TRACE_REPORT_SCHEMA_VERSION = 2


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)
    temporary.replace(path)


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _key_id(key):
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:16]


def resolve_pdftoppm(poppler_bin=None):
    candidates = []
    explicit = poppler_bin or os.environ.get("POPPLER_BIN")
    if explicit:
        value = Path(explicit)
        candidates.append(value / "pdftoppm.exe" if value.is_dir() else value)
        candidates.append(value / "pdftoppm" if value.is_dir() else value)
    # Windows PATH may contain a broken wrapper before the real executable.
    # Prefer an actual .exe when one is available.
    located_executable = shutil.which("pdftoppm.exe")
    if located_executable:
        candidates.append(Path(located_executable))
    located = shutil.which("pdftoppm")
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "未找到pdftoppm。请安装Poppler，或通过--poppler-bin指定其bin目录。"
    )


def render_pdf_pages(pdf_path, output_dir, dpi=150, poppler_bin=None):
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF不存在: {pdf_path}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    executable = resolve_pdftoppm(poppler_bin)
    prefix = output_dir / "render"
    command = [
        str(executable), "-png", "-r", str(int(dpi)),
        str(pdf_path), str(prefix),
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "PDF渲染失败: " + (completed.stderr.strip() or completed.stdout.strip())
        )
    rendered = sorted(output_dir.glob("render-*.png"))
    if not rendered:
        raise RuntimeError("PDF渲染没有产生页面")
    pages = []
    for index, source in enumerate(rendered, 1):
        target = output_dir / f"page_{index:03d}.png"
        source.replace(target)
        pages.append(target)
    return pages, completed.stderr.strip()


def _adaptive_alpha(block, base_alpha):
    mean = float(np.mean(block))
    deviation = float(np.std(block))
    if mean >= 247.0 and deviation <= 2.5:
        scale = 0.60
    elif deviation < 7.0:
        scale = 0.78
    elif deviation > 35.0:
        scale = 1.12
    else:
        scale = 1.0
    return max(12.0, float(base_alpha) * scale)


def embed_bits_adaptive(
    image,
    bits,
    key,
    alpha,
    repeat,
    block_size=8,
    position_offset=0,
):
    if image is None:
        raise ValueError("输入页面为空")
    height, width = image.shape[:2]
    total_count = int(position_offset) + len(bits) * int(repeat)
    positions = generate_positions(
        height, width, key, total_count, block_size=int(block_size)
    )
    ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = cv2.split(ycrcb)
    index = int(position_offset)
    alpha_values = []
    for bit in bits:
        for _ in range(int(repeat)):
            row, col = positions[index]
            index += 1
            y1 = row * int(block_size)
            x1 = col * int(block_size)
            block = y[y1:y1 + int(block_size), x1:x1 + int(block_size)]
            local_alpha = _adaptive_alpha(block, alpha)
            alpha_values.append(local_alpha)
            y[y1:y1 + int(block_size), x1:x1 + int(block_size)] = embed_bit(
                block, int(bit), alpha=local_alpha
            )
    result = cv2.cvtColor(cv2.merge([y, cr, cb]), cv2.COLOR_YCrCb2BGR)
    return result, {
        "alpha_min": float(min(alpha_values)),
        "alpha_max": float(max(alpha_values)),
        "alpha_mean": float(np.mean(alpha_values)),
        "dct_units": len(alpha_values),
    }


def _save_raster_pdf(page_images, output_pdf, dpi):
    pil_pages = []
    try:
        for page in page_images:
            pil_pages.append(Image.open(page).convert("RGB"))
        if not pil_pages:
            raise ValueError("没有可写入PDF的页面")
        output_pdf = Path(output_pdf)
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_pdf.with_suffix(".tmp.pdf")
        pil_pages[0].save(
            temporary,
            format="PDF",
            save_all=True,
            append_images=pil_pages[1:],
            resolution=float(dpi),
            quality=95,
            subsampling=0,
            title=output_pdf.stem,
            subject="V2.0A anti-screen document watermark derivative",
            creator="anti_screen_watermark_pdf",
        )
        temporary.replace(output_pdf)
    finally:
        for page in pil_pages:
            page.close()


def embed_document_pdf(
    input_pdf,
    registry_path,
    key=DEFAULT_KEY,
    output_pdf=None,
    assets_root=None,
    dpi=150,
    alpha=42.0,
    repeat=16,
    pilot_bits=64,
    pilot_repeat=6,
    pilot_alpha=78.0,
    poppler_bin=None,
    recipient=None,
    session=None,
    notes=None,
    trace_token=None,
    watermark_number=None,
    source_name=None,
):
    started = time.perf_counter()
    input_pdf = Path(input_pdf).resolve()
    registry_path = Path(registry_path).resolve()
    source_sha256 = sha256_file(input_pdf)
    provisional_document_id = source_sha256[:24]
    if assets_root is None:
        assets_root = input_pdf.parent / ".document_assets"
    document_assets = Path(assets_root).resolve() / provisional_document_id
    reference_dir = document_assets / "reference_pages"
    reference_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pdf_v20a_render_") as temporary_dir:
        rendered, render_warnings = render_pdf_pages(
            input_pdf, temporary_dir, dpi=dpi, poppler_bin=poppler_bin
        )
        page_records = []
        first_shape = None
        for index, rendered_page in enumerate(rendered, 1):
            image = cv2.imread(str(rendered_page), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError(f"无法读取渲染页面: {rendered_page}")
            height, width = image.shape[:2]
            first_shape = first_shape or (height, width)
            target = reference_dir / f"page_{index:03d}.png"
            if not cv2.imwrite(str(target), image):
                raise RuntimeError(f"无法保存参考页面: {target}")
            page_records.append({
                "page_index": index,
                "width": width,
                "height": height,
                "reference_path": str(target.resolve()),
                "reference_sha256": sha256_file(target),
            })

        media_box = [
            float(first_shape[1]) * 72.0 / float(dpi),
            float(first_shape[0]) * 72.0 / float(dpi),
        ]
        document_id, document = register_document(
            registry_path,
            input_pdf,
            page_records,
            dpi,
            media_box,
            document_assets,
            source_name=source_name,
        )
        issue = issue_document_trace(
            registry_path,
            document_id,
            trace_token=trace_token,
            watermark_number=watermark_number,
            metadata={
                "recipient": recipient,
                "session": session,
                "notes": notes,
                "key_id": _key_id(key),
            },
        )
        token = issue["trace_token"]
        if output_pdf is None:
            public_number = issue.get("watermark_number") or token
            output_pdf = input_pdf.with_name(
                f"{input_pdf.stem}_wm_{public_number}.pdf"
            )
        output_pdf = Path(output_pdf).resolve()
        manifest_path = output_pdf.with_suffix(".manifest.json")
        payload = encode_document_token(token)

        issue_page_dir = document_assets / "issues" / token
        issue_page_dir.mkdir(parents=True, exist_ok=True)
        embedded_pages = []
        manifest_pages = []
        for page_record in page_records:
            page_index = page_record["page_index"]
            image = cv2.imread(page_record["reference_path"], cv2.IMREAD_COLOR)
            page_key = derive_page_key(key, document_id, page_index)
            watermarked, payload_stats = embed_bits_adaptive(
                image,
                payload,
                page_key,
                alpha=alpha,
                repeat=repeat,
                block_size=8,
                position_offset=0,
            )
            pilot = generate_sync_pilot(page_key, int(pilot_bits))
            watermarked, pilot_stats = embed_bits_adaptive(
                watermarked,
                pilot,
                page_key,
                alpha=pilot_alpha,
                repeat=pilot_repeat,
                block_size=8,
                position_offset=DOCUMENT_CODEWORD_BITS * int(repeat),
            )
            target = issue_page_dir / f"page_{page_index:03d}_watermarked.png"
            if not cv2.imwrite(str(target), watermarked):
                raise RuntimeError(f"无法保存水印页面: {target}")
            embedded_pages.append(target)
            manifest_pages.append({
                **page_record,
                "watermarked_page_sha256": sha256_file(target),
                "payload_embedding": payload_stats,
                "pilot_embedding": pilot_stats,
            })

        _save_raster_pdf(embedded_pages, output_pdf, dpi=dpi)
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "watermark_version": DOCUMENT_WATERMARK_VERSION,
            "source_pdf": str(input_pdf),
            "source_sha256": source_sha256,
            "output_pdf": str(output_pdf),
            "output_sha256": sha256_file(output_pdf),
            "document_id": document_id,
            "page_count": len(page_records),
            "dpi": int(dpi),
            "media_box_points": media_box,
            "trace_id": issue["trace_id"],
            "trace_token": token,
            "watermark_number": issue.get("watermark_number"),
            "encoded_bits": [int(bit) for bit in payload],
            "key_id": _key_id(key),
            "watermark": {
                "block_size": 8,
                "bit_count": DOCUMENT_CODEWORD_BITS,
                "alpha": float(alpha),
                "repeat": int(repeat),
                "adaptive_alpha": True,
                "sync_pilot": {
                    "enabled": True,
                    "version": "pn64-v1",
                    "bit_count": int(pilot_bits),
                    "repeat": int(pilot_repeat),
                    "alpha": float(pilot_alpha),
                    "position_offset": DOCUMENT_CODEWORD_BITS * int(repeat),
                },
            },
            "pages": manifest_pages,
            "render_warnings": render_warnings.splitlines() if render_warnings else [],
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        }
        _write_json(manifest_path, manifest)
        attach_issue_artifact(registry_path, token, output_pdf, manifest_path)
        return manifest_path, manifest


def aggregate_trimmed_repeat_scores(details, trim_ratio=0.20):
    """用截尾均值聚合重复副本，抑制屏摄摩尔纹造成的极端DCT分数。"""

    if not 0.0 <= float(trim_ratio) < 0.5:
        raise ValueError("trim_ratio必须在[0, 0.5)范围内")
    min_valid_repeats = int(details.get("min_valid_repeats", 3))
    scores = []
    trim_counts = []

    for repeated in details.get("repeat_scores", []):
        valid = np.asarray([
            float(value)
            for value in repeated
            if value is not None and np.isfinite(float(value))
        ], dtype=np.float32)
        if len(valid) < min_valid_repeats:
            scores.append(None)
            trim_counts.append(0)
            continue
        valid.sort()
        requested = int(np.floor(len(valid) * float(trim_ratio)))
        maximum = max(0, (len(valid) - min_valid_repeats) // 2)
        trim_count = min(requested, maximum)
        selected = (
            valid[trim_count:len(valid) - trim_count]
            if trim_count > 0
            else valid
        )
        scores.append(float(np.mean(selected)))
        trim_counts.append(int(trim_count))

    aggregation = {
        "method": "trimmed_mean",
        "trim_ratio": float(trim_ratio),
        "trim_counts": trim_counts,
        "observed_bits": int(sum(score is not None for score in scores)),
    }
    return scores, aggregation


def _extract_page_scores(
    image,
    valid_mask,
    page_key,
    watermark_config,
    trim_ratio=0.20,
):
    bits, scores, details = extract_watermark_with_erasure(
        image,
        valid_mask,
        bit_count=int(watermark_config["bit_count"]),
        key=page_key,
        repeat=int(watermark_config["repeat"]),
        block_size=int(watermark_config["block_size"]),
        min_block_coverage=0.80,
        min_valid_repeats=3,
    )
    median_scores = scores
    scores, aggregation = aggregate_trimmed_repeat_scores(
        details, trim_ratio=trim_ratio
    )
    bits = [
        None if score is None else int(float(score) > 0.0)
        for score in scores
    ]
    details["aggregation"] = aggregation
    details["median_scores"] = median_scores
    decoded = decode_document_soft_scores(scores)
    return bits, scores, details, decoded


def _inclusive_values(center, radius, step):
    center = float(center)
    radius = float(radius)
    step = float(step)
    if radius < 0.0 or step <= 0.0:
        raise ValueError("搜索半径不能为负且步长必须大于0")
    count = int(round((2.0 * radius) / step))
    return [
        round(center - radius + index * step, 6)
        for index in range(count + 1)
    ]


def _document_candidate_rank(record):
    return (
        int(bool(record.get("accepted"))),
        float(record.get("z_score", -np.inf)),
        float(record.get("normalized_score", -np.inf)),
        float(record.get("hard_match_rate", 0.0)),
        float(record.get("margin_z", -np.inf)),
    )


def _registry_decision_rank(decision):
    """Return the same conservative ordering used by registry reranking."""

    selected = (decision or {}).get("selected") or {}
    return _document_candidate_rank({
        "accepted": bool((decision or {}).get("accepted")),
        "z_score": selected.get("z_score", -np.inf),
        "normalized_score": selected.get("normalized_score", -np.inf),
        "hard_match_rate": selected.get("hard_match_rate", 0.0),
        "margin_z": selected.get("margin_z", -np.inf),
    })


def _decision_summary(decision):
    selected = (decision or {}).get("selected") or {}
    return {
        "accepted": bool((decision or {}).get("accepted")),
        "status": (decision or {}).get("status"),
        "trace_token": selected.get("trace_token"),
        "trace_id": selected.get("trace_id"),
        "observed_bits": int(selected.get("observed_bits", 0)),
        "normalized_score": float(selected.get("normalized_score", -np.inf)),
        "z_score": float(selected.get("z_score", -np.inf)),
        "hard_match_rate": float(selected.get("hard_match_rate", 0.0)),
        "margin_z": float(selected.get("margin_z", -np.inf)),
    }


def rerank_document_sync_candidates(
    image,
    valid_mask,
    page_key,
    watermark_config,
    registry,
    document_id,
    synchronization,
    trim_ratio=0.20,
    seed_count=2,
    fine_scale_radius=0.004,
    fine_scale_step=0.001,
    fine_translation_radius=1.5,
    fine_translation_step=0.5,
):
    """以合法TraceToken软分数重排导频Top-K并执行亚像素分层精搜索。"""

    if int(seed_count) < 1:
        raise ValueError("seed_count必须至少为1")
    candidates = {}

    def evaluate(scale, dx, dy, stage, seed_index=None, pilot_correlation=None):
        key = (
            round(float(scale), 6),
            round(float(dx), 3),
            round(float(dy), 3),
        )
        existing = candidates.get(key)
        if existing is not None:
            if stage not in existing["stages"]:
                existing["stages"].append(stage)
            return existing
        warped, warped_mask, matrix = _warp_candidate(
            image, valid_mask, key[0], key[1], key[2]
        )
        _, scores, _, _ = _extract_page_scores(
            warped,
            warped_mask,
            page_key,
            watermark_config,
            trim_ratio=trim_ratio,
        )
        decision = score_registered_tokens(
            scores, registry, document_id=document_id
        )
        selected = decision.get("selected") or {}
        record = {
            "scale": key[0],
            "dx": key[1],
            "dy": key[2],
            "matrix": matrix.tolist(),
            "stages": [stage],
            "seed_index": seed_index,
            "source_pilot_correlation": pilot_correlation,
            "accepted": bool(decision.get("accepted")),
            "decision_status": decision.get("status"),
            "trace_token": selected.get("trace_token"),
            "trace_id": selected.get("trace_id"),
            "observed_bits": int(selected.get("observed_bits", 0)),
            "normalized_score": float(selected.get("normalized_score", -np.inf)),
            "z_score": float(selected.get("z_score", -np.inf)),
            "hard_match_rate": float(selected.get("hard_match_rate", 0.0)),
            "margin_z": float(selected.get("margin_z", -np.inf)),
        }
        candidates[key] = record
        return record

    pilot_candidates = list(synchronization.get("top_candidates") or [])
    if not pilot_candidates:
        pilot_candidates = [{
            "scale": 1.0,
            "dx": 0.0,
            "dy": 0.0,
            "normalized_correlation": None,
        }]
    elif not any(
        abs(float(item.get("scale", 1.0)) - 1.0) < 1e-9
        and abs(float(item.get("dx", 0.0))) < 1e-9
        and abs(float(item.get("dy", 0.0))) < 1e-9
        for item in pilot_candidates
    ):
        # Low-coverage pilot ranking can omit the unmodified ORB result.  Keep
        # identity as a mandatory safety fallback so reranking never discards
        # a better baseline merely because pilot evidence is sparse.
        pilot_candidates.append({
            "scale": 1.0,
            "dx": 0.0,
            "dy": 0.0,
            "normalized_correlation": None,
        })
    coarse_records = [
        evaluate(
            item["scale"],
            item["dx"],
            item["dy"],
            "pilot_top_k",
            pilot_correlation=item.get("normalized_correlation"),
        )
        for item in pilot_candidates
    ]
    coarse_ranked = sorted(coarse_records, key=_document_candidate_rank, reverse=True)

    seeds = []
    seen_tokens = set()
    for record in coarse_ranked:
        token = record.get("trace_token")
        if token is None or token in seen_tokens:
            continue
        seeds.append(record)
        seen_tokens.add(token)
        if len(seeds) >= int(seed_count):
            break
    for record in coarse_ranked:
        if len(seeds) >= int(seed_count):
            break
        if record not in seeds:
            seeds.append(record)

    fine_evaluation_count = 0
    for seed_index, seed in enumerate(seeds):
        scale_records = []
        for scale in _inclusive_values(
            seed["scale"], fine_scale_radius, fine_scale_step
        ):
            scale_records.append(evaluate(
                scale,
                seed["dx"],
                seed["dy"],
                "fine_scale",
                seed_index=seed_index,
                pilot_correlation=seed.get("source_pilot_correlation"),
            ))
            fine_evaluation_count += 1
        best_scale = max(scale_records, key=_document_candidate_rank)

        translation_records = []
        for dx in _inclusive_values(
            seed["dx"], fine_translation_radius, fine_translation_step
        ):
            for dy in _inclusive_values(
                seed["dy"], fine_translation_radius, fine_translation_step
            ):
                translation_records.append(evaluate(
                    best_scale["scale"],
                    dx,
                    dy,
                    "fine_translation",
                    seed_index=seed_index,
                    pilot_correlation=seed.get("source_pilot_correlation"),
                ))
                fine_evaluation_count += 1
        best_translation = max(
            translation_records, key=_document_candidate_rank
        )

        for scale in _inclusive_values(
            best_translation["scale"], fine_scale_step, fine_scale_step
        ):
            for dx in _inclusive_values(
                best_translation["dx"], fine_translation_step, fine_translation_step
            ):
                for dy in _inclusive_values(
                    best_translation["dy"], fine_translation_step, fine_translation_step
                ):
                    evaluate(
                        scale,
                        dx,
                        dy,
                        "joint_polish",
                        seed_index=seed_index,
                        pilot_correlation=seed.get("source_pilot_correlation"),
                    )
                    fine_evaluation_count += 1

    ranked = sorted(candidates.values(), key=_document_candidate_rank, reverse=True)
    selected_transform = ranked[0]
    selected_image, selected_mask, selected_matrix = _warp_candidate(
        image,
        valid_mask,
        selected_transform["scale"],
        selected_transform["dx"],
        selected_transform["dy"],
    )
    bits, scores, details, decoded = _extract_page_scores(
        selected_image,
        selected_mask,
        page_key,
        watermark_config,
        trim_ratio=trim_ratio,
    )
    decision = score_registered_tokens(
        scores, registry, document_id=document_id
    )
    report = {
        "enabled": True,
        "version": "v2.0a.1-document-registry-rerank",
        "aggregation": {
            "method": "trimmed_mean",
            "trim_ratio": float(trim_ratio),
        },
        "coarse_candidate_count": len(coarse_records),
        "fine_evaluation_count": int(fine_evaluation_count),
        "unique_candidate_count": len(candidates),
        "seed_count": len(seeds),
        "fine_scale_radius": float(fine_scale_radius),
        "fine_scale_step": float(fine_scale_step),
        "fine_translation_radius": float(fine_translation_radius),
        "fine_translation_step": float(fine_translation_step),
        "seeds": seeds,
        "selected": {
            **selected_transform,
            "matrix": selected_matrix.tolist(),
        },
        "top_candidates": ranked[:12],
        "decision_status": decision.get("status"),
        "accepted": bool(decision.get("accepted")),
    }
    return (
        selected_image,
        selected_mask,
        bits,
        scores,
        details,
        decoded,
        decision,
        report,
    )


def _warp_local_candidate(image, valid_mask, scale, dx, dy, center):
    """Apply a residual affine correction around a local tile centre."""

    height, width = image.shape[:2]
    center_x, center_y = float(center[0]), float(center[1])
    matrix = np.float32([
        [float(scale), 0.0, float(dx) + (1.0 - float(scale)) * center_x],
        [0.0, float(scale), float(dy) + (1.0 - float(scale)) * center_y],
    ])
    warped = cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )
    warped_mask = cv2.warpAffine(
        valid_mask,
        matrix,
        (width, height),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return warped, warped_mask, matrix


def build_local_partition_masks(valid_mask, block_size=8, min_page_coverage=0.04):
    """Build canonical non-overlapping quadrants plus diagnostic half-page tiles."""

    if valid_mask is None or valid_mask.ndim != 2:
        raise ValueError("局部分区需要单通道有效区域Mask")
    height, width = valid_mask.shape[:2]
    block_size = int(block_size)
    middle_x = max(block_size, (width // (2 * block_size)) * block_size)
    middle_y = max(block_size, (height // (2 * block_size)) * block_size)
    specifications = [
        ("quadrant_tl", 0, 0, middle_x, middle_y, True),
        ("quadrant_tr", middle_x, 0, width, middle_y, True),
        ("quadrant_bl", 0, middle_y, middle_x, height, True),
        ("quadrant_br", middle_x, middle_y, width, height, True),
        ("half_left", 0, 0, middle_x, height, False),
        ("half_right", middle_x, 0, width, height, False),
        ("half_top", 0, 0, width, middle_y, False),
        ("half_bottom", 0, middle_y, width, height, False),
    ]
    partitions = []
    for name, x1, y1, x2, y2, fusion_member in specifications:
        region = np.zeros_like(valid_mask, dtype=np.uint8)
        region[y1:y2, x1:x2] = valid_mask[y1:y2, x1:x2]
        page_coverage = float(np.mean(region >= 128))
        if page_coverage < float(min_page_coverage):
            continue
        tile_area = max(1, (x2 - x1) * (y2 - y1))
        visible_in_tile = float(np.count_nonzero(region >= 128) / tile_area)
        partitions.append({
            "name": name,
            "bounds": [int(x1), int(y1), int(x2), int(y2)],
            "center": [(x1 + x2 - 1) / 2.0, (y1 + y2 - 1) / 2.0],
            "page_coverage": page_coverage,
            "visible_in_tile": visible_in_tile,
            "fusion_member": bool(fusion_member),
            "mask": region,
        })
    return partitions


def _normalize_soft_scores(scores):
    values = [
        abs(float(value))
        for value in scores
        if value is not None and np.isfinite(float(value))
    ]
    scale = float(np.median(values)) if values else 1.0
    scale = max(scale, 1e-6)
    return [
        None if value is None else float(value) / scale
        for value in scores
    ], scale


def fuse_partition_soft_scores(partitions, bit_count=DOCUMENT_CODEWORD_BITS):
    """Fuse independently synchronized partition scores without double counting."""

    prepared = []
    for partition in partitions:
        normalized, score_scale = _normalize_soft_scores(partition["scores"])
        pilot = partition.get("pilot") or {}
        observed_ratio = min(1.0, float(pilot.get("observed_bits", 0)) / 64.0)
        correlation = max(0.0, float(pilot.get("normalized_correlation", 0.0)))
        match_quality = max(0.05, 2.0 * (float(pilot.get("match_rate", 0.5)) - 0.5))
        weight = max(0.02, correlation * np.sqrt(observed_ratio) * match_quality)
        prepared.append({
            **partition,
            "normalized_scores": normalized,
            "score_scale": score_scale,
            "fusion_weight": float(weight),
        })

    fused = []
    contributor_counts = []
    for bit_index in range(int(bit_count)):
        numerator = 0.0
        denominator = 0.0
        contributors = 0
        for partition in prepared:
            value = partition["normalized_scores"][bit_index]
            if value is None or not np.isfinite(float(value)):
                continue
            weight = float(partition["fusion_weight"])
            numerator += weight * float(value)
            denominator += weight
            contributors += 1
        fused.append(None if denominator <= 0.0 else float(numerator / denominator))
        contributor_counts.append(contributors)
    diagnostics = {
        "method": "quality_weighted_partition_mean",
        "partition_count": len(prepared),
        "observed_bits": int(sum(value is not None for value in fused)),
        "contributor_counts": contributor_counts,
        "partitions": [{
            "name": item["name"],
            "fusion_weight": item["fusion_weight"],
            "score_scale": item["score_scale"],
        } for item in prepared],
    }
    return fused, diagnostics


def _blend_soft_scores(first, second, second_weight):
    first_normalized, _ = _normalize_soft_scores(first)
    second_normalized, _ = _normalize_soft_scores(second)
    second_weight = float(second_weight)
    blended = []
    for left, right in zip(first_normalized, second_normalized):
        if left is None:
            blended.append(right)
        elif right is None:
            blended.append(left)
        else:
            blended.append(
                (1.0 - second_weight) * float(left)
                + second_weight * float(right)
            )
    return blended


def local_partition_sync_and_fuse(
    image,
    valid_mask,
    page_key,
    watermark_config,
    registry,
    document_id,
    global_bits,
    global_scores,
    global_details,
    global_decoded,
    global_decision,
    trim_ratio=0.20,
    payload_top_k=27,
    residual_scales=(0.997, 1.0, 1.003),
    residual_translations=(-1.5, 0.0, 1.5),
    min_tile_pilot_bits=12,
):
    """V2.0A.2: synchronize canonical page partitions and fuse soft payloads."""

    sync_config = watermark_config.get("sync_pilot") or {}
    if not sync_config.get("enabled", False):
        return (
            global_bits, global_scores, global_details, global_decoded,
            global_decision, {"enabled": False, "status": "NO_SYNC_PILOT"},
        )
    payload_top_k = max(1, int(payload_top_k))
    partitions = build_local_partition_masks(
        valid_mask,
        block_size=int(watermark_config["block_size"]),
    )
    tile_sources = []
    tile_reports = []
    pilot_evaluation_count = 0
    payload_evaluation_count = 0

    for partition in partitions:
        pilot_candidates = []
        for scale in residual_scales:
            for dx in residual_translations:
                for dy in residual_translations:
                    warped, warped_mask, matrix = _warp_local_candidate(
                        image,
                        valid_mask,
                        scale,
                        dx,
                        dy,
                        partition["center"],
                    )
                    extraction_mask = cv2.bitwise_and(
                        warped_mask, partition["mask"]
                    )
                    pilot = _pilot_measure(
                        warped,
                        extraction_mask,
                        page_key,
                        int(watermark_config["block_size"]),
                        sync_config,
                    )
                    pilot_evaluation_count += 1
                    transform_cost = (
                        abs(float(scale) - 1.0) * 1000.0
                        + float(dx) * float(dx)
                        + float(dy) * float(dy)
                    )
                    pilot_candidates.append({
                        "scale": float(scale),
                        "dx": float(dx),
                        "dy": float(dy),
                        "matrix": matrix.tolist(),
                        "transform_cost": transform_cost,
                        **pilot,
                    })
        pilot_candidates.sort(
            key=lambda item: (
                int(item["observed_bits"] >= int(min_tile_pilot_bits)),
                round(float(item["normalized_correlation"]), 6),
                round(float(item["match_rate"]), 6),
                int(item["observed_bits"]),
                -float(item["transform_cost"]),
            ),
            reverse=True,
        )
        payload_records = []
        for pilot in pilot_candidates[:payload_top_k]:
            if int(pilot["observed_bits"]) < int(min_tile_pilot_bits):
                continue
            warped, warped_mask, _ = _warp_local_candidate(
                image,
                valid_mask,
                pilot["scale"],
                pilot["dx"],
                pilot["dy"],
                partition["center"],
            )
            extraction_mask = cv2.bitwise_and(warped_mask, partition["mask"])
            bits, scores, details, decoded = _extract_page_scores(
                warped,
                extraction_mask,
                page_key,
                watermark_config,
                trim_ratio=trim_ratio,
            )
            decision = score_registered_tokens(
                scores, registry, document_id=document_id
            )
            payload_evaluation_count += 1
            payload_records.append({
                "pilot": pilot,
                "bits": bits,
                "scores": scores,
                "details": details,
                "decoded": decoded,
                "decision": decision,
            })
        if not payload_records:
            tile_reports.append({
                "name": partition["name"],
                "bounds": partition["bounds"],
                "page_coverage": partition["page_coverage"],
                "visible_in_tile": partition["visible_in_tile"],
                "status": "SKIPPED_INSUFFICIENT_PILOT",
                "best_pilot": pilot_candidates[0] if pilot_candidates else None,
            })
            continue
        selected = max(
            payload_records,
            key=lambda item: _registry_decision_rank(item["decision"]),
        )
        source = {
            "name": partition["name"],
            "fusion_member": partition["fusion_member"],
            "scores": selected["scores"],
            "pilot": selected["pilot"],
            "decision": selected["decision"],
            "details": selected["details"],
        }
        tile_sources.append(source)
        tile_reports.append({
            "name": partition["name"],
            "bounds": partition["bounds"],
            "page_coverage": partition["page_coverage"],
            "visible_in_tile": partition["visible_in_tile"],
            "status": "EVALUATED",
            "selected_transform": {
                key: selected["pilot"][key]
                for key in ("scale", "dx", "dy", "matrix")
            },
            "selected_pilot": {
                key: selected["pilot"][key]
                for key in (
                    "observed_bits", "match_rate", "normalized_correlation",
                    "mean_signed_correlation", "valid_dct_unit_ratio",
                )
            },
            "decision": _decision_summary(selected["decision"]),
            "top_pilot_candidates": pilot_candidates[:payload_top_k],
        })

    score_candidates = [{
        "name": "global",
        "scores": global_scores,
        "bits": global_bits,
        "details": global_details,
        "decoded": global_decoded,
        "decision": global_decision,
    }]
    for source in tile_sources:
        bits = [
            None if value is None else int(float(value) > 0.0)
            for value in source["scores"]
        ]
        score_candidates.append({
            "name": source["name"],
            "scores": source["scores"],
            "bits": bits,
            "details": source["details"],
            "decoded": decode_document_soft_scores(source["scores"]),
            "decision": source["decision"],
        })

    fusion_members = [
        source for source in tile_sources if source["fusion_member"]
    ]
    fusion_diagnostics = None
    if fusion_members:
        fused_scores, fusion_diagnostics = fuse_partition_soft_scores(
            fusion_members,
            bit_count=int(watermark_config["bit_count"]),
        )
        fusion_variants = [("partition_fusion", fused_scores)]
        for local_weight in (0.50, 0.75):
            fusion_variants.append((
                f"global_local_blend_{int(local_weight * 100)}",
                _blend_soft_scores(global_scores, fused_scores, local_weight),
            ))
        for name, scores in fusion_variants:
            bits = [
                None if value is None else int(float(value) > 0.0)
                for value in scores
            ]
            details = dict(global_details)
            details["aggregation"] = {
                "method": name,
                "observed_bits": int(sum(value is not None for value in scores)),
            }
            score_candidates.append({
                "name": name,
                "scores": scores,
                "bits": bits,
                "details": details,
                "decoded": decode_document_soft_scores(scores),
                "decision": score_registered_tokens(
                    scores, registry, document_id=document_id
                ),
            })

    selected = max(
        score_candidates,
        key=lambda item: _registry_decision_rank(item["decision"]),
    )
    final_details = dict(selected["details"])
    final_details["local_partition_selection"] = {
        "score_source": selected["name"],
        "global_valid_dct_unit_ratio": global_details.get(
            "valid_dct_unit_ratio"
        ),
    }
    report = {
        "enabled": True,
        "version": "v2.0a.2-local-partition-sync",
        "status": (
            "ACCEPTED" if selected["decision"].get("accepted")
            else "COMPLETED"
        ),
        "selected_score_source": selected["name"],
        "pilot_evaluation_count": int(pilot_evaluation_count),
        "payload_evaluation_count": int(payload_evaluation_count),
        "residual_scales": [float(value) for value in residual_scales],
        "residual_translations": [float(value) for value in residual_translations],
        "min_tile_pilot_bits": int(min_tile_pilot_bits),
        "tile_reports": tile_reports,
        "fusion": fusion_diagnostics,
        "score_candidates": [{
            "name": item["name"],
            "decision": _decision_summary(item["decision"]),
        } for item in score_candidates],
        "selected_decision": _decision_summary(selected["decision"]),
    }
    return (
        selected["bits"],
        selected["scores"],
        final_details,
        selected["decoded"],
        selected["decision"],
        report,
    )


def extract_digital_pdf(
    pdf_path,
    manifest_path,
    registry_path,
    key=DEFAULT_KEY,
    poppler_bin=None,
    report_path=None,
):
    started = time.perf_counter()
    manifest = _read_json(manifest_path)
    if manifest.get("key_id") != _key_id(key):
        raise ValueError("密钥与Manifest的key_id不一致")
    registry = load_document_registry(registry_path)
    with tempfile.TemporaryDirectory(prefix="pdf_v20a_extract_") as temporary_dir:
        rendered, warnings = render_pdf_pages(
            pdf_path,
            temporary_dir,
            dpi=int(manifest["dpi"]),
            poppler_bin=poppler_bin,
        )
        if len(rendered) != int(manifest["page_count"]):
            raise RuntimeError("水印PDF页数与Manifest不一致")
        results = []
        for index, page_path in enumerate(rendered, 1):
            expected_page = manifest["pages"][index - 1]
            image = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
            expected_size = (int(expected_page["width"]), int(expected_page["height"]))
            if (image.shape[1], image.shape[0]) != expected_size:
                image = cv2.resize(image, expected_size, interpolation=cv2.INTER_CUBIC)
            valid_mask = np.full(image.shape[:2], 255, dtype=np.uint8)
            page_key = derive_page_key(key, manifest["document_id"], index)
            bits, scores, details, decoded = _extract_page_scores(
                image, valid_mask, page_key, manifest["watermark"]
            )
            registry_decision = score_registered_tokens(
                scores, registry, document_id=manifest["document_id"]
            )
            token_ok = decoded.get("trace_token") == manifest["trace_token"]
            results.append({
                "page_index": index,
                "crc_pass": bool(decoded.get("crc_pass")),
                "recovered_trace_token": decoded.get("trace_token"),
                "expected_trace_token": manifest["trace_token"],
                "trace_match": bool(decoded.get("crc_pass") and token_ok),
                "observed_bits": int(sum(bit is not None for bit in bits)),
                "erasures": int(sum(bit is None for bit in bits)),
                "confidence_p50": float(np.median([
                    abs(float(score)) for score in scores if score is not None
                ])),
                "registry_decision": registry_decision,
                "extraction": details,
            })
        report = {
            "schema_version": TRACE_REPORT_SCHEMA_VERSION,
            "mode": "digital_pdf",
            "evaluated_at": _now_iso(),
            "pdf_path": str(Path(pdf_path).resolve()),
            "manifest_path": str(Path(manifest_path).resolve()),
            "page_count": len(results),
            "passed_pages": sum(item["trace_match"] for item in results),
            "all_pages_pass": all(item["trace_match"] for item in results),
            "pages": results,
            "render_warnings": warnings.splitlines() if warnings else [],
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        }
    if report_path:
        _write_json(report_path, report)
    return report


def _resize_for_features(image, maximum=2200):
    height, width = image.shape[:2]
    scale = min(1.0, float(maximum) / float(max(height, width)))
    if scale >= 0.9999:
        return image, 1.0
    resized = cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def _orb_homography(photo, reference):
    query, query_scale = _resize_for_features(photo)
    reference_small, reference_scale = _resize_for_features(reference)
    query_gray = cv2.cvtColor(query, cv2.COLOR_BGR2GRAY)
    reference_gray = cv2.cvtColor(reference_small, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(
        nfeatures=7000,
        scaleFactor=1.2,
        nlevels=10,
        edgeThreshold=15,
        patchSize=31,
        fastThreshold=7,
    )
    query_points, query_descriptors = orb.detectAndCompute(query_gray, None)
    reference_points, reference_descriptors = orb.detectAndCompute(reference_gray, None)
    if query_descriptors is None or reference_descriptors is None:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(query_descriptors, reference_descriptors, k=2)
    good = [first for first, second in pairs if first.distance < 0.76 * second.distance]
    if len(good) < 12:
        return None
    source = np.float32([query_points[item.queryIdx].pt for item in good]).reshape(-1, 1, 2)
    destination = np.float32([
        reference_points[item.trainIdx].pt for item in good
    ]).reshape(-1, 1, 2)
    homography_small, inlier_mask = cv2.findHomography(
        source, destination, cv2.RANSAC, 4.0
    )
    if homography_small is None or inlier_mask is None:
        return None
    inliers = int(np.sum(inlier_mask))
    if inliers < 10:
        return None
    query_to_small = np.diag([query_scale, query_scale, 1.0])
    small_to_reference = np.diag([
        1.0 / reference_scale, 1.0 / reference_scale, 1.0
    ])
    homography = small_to_reference @ homography_small @ query_to_small
    projected = cv2.perspectiveTransform(source, homography_small)
    errors = np.linalg.norm(projected - destination, axis=2).reshape(-1)
    selected_errors = errors[inlier_mask.reshape(-1).astype(bool)]
    return {
        "homography": homography,
        "matches": len(good),
        "inliers": inliers,
        "inlier_ratio": float(inliers / len(good)),
        "reprojection_median": float(np.median(selected_errors)),
        "method": "ORB_RANSAC",
    }


def _order_quad(points):
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    total = points.sum(axis=1)
    difference = np.diff(points, axis=1).reshape(-1)
    return np.asarray([
        points[np.argmin(total)],
        points[np.argmin(difference)],
        points[np.argmax(total)],
        points[np.argmax(difference)],
    ], dtype=np.float32)


def detect_page_boundary(photo):
    gray = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 40, 130)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = float(photo.shape[0] * photo.shape[1])
    candidates = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:30]:
        area = float(cv2.contourArea(contour))
        if area < image_area * 0.18:
            continue
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(polygon) == 4 and cv2.isContourConvex(polygon):
            candidates.append((area, _order_quad(polygon[:, 0, :])))
    return candidates[0][1] if candidates else None


def _boundary_homography(photo, reference):
    quad = detect_page_boundary(photo)
    if quad is None:
        return None
    height, width = reference.shape[:2]
    target = np.float32([
        [0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]
    ])
    homography = cv2.getPerspectiveTransform(quad, target)
    rectified = cv2.warpPerspective(photo, homography, (width, height))
    refinement = _orb_homography(rectified, reference)
    if refinement is not None:
        homography = refinement["homography"] @ homography
        matches = refinement["matches"]
        inliers = refinement["inliers"]
        ratio = refinement["inlier_ratio"]
        reprojection = refinement["reprojection_median"]
    else:
        matches = inliers = 4
        ratio = 1.0
        reprojection = 0.0
    return {
        "homography": homography,
        "matches": matches,
        "inliers": inliers,
        "inlier_ratio": ratio,
        "reprojection_median": reprojection,
        "method": "PAGE_BOUNDARY" if refinement is None else "PAGE_BOUNDARY_ORB",
    }


def match_photo_to_registered_page(photo, registry):
    candidates = []
    for document_id, document in registry.get("documents", {}).items():
        if document.get("status", "active") != "active":
            continue
        for page in document.get("pages", []):
            reference = cv2.imread(page["reference_path"], cv2.IMREAD_COLOR)
            if reference is None:
                continue
            alignment = _orb_homography(photo, reference)
            if alignment is None:
                continue
            candidates.append({
                **alignment,
                "document_id": document_id,
                "page_index": int(page["page_index"]),
                "reference_path": page["reference_path"],
                "width": int(page["width"]),
                "height": int(page["height"]),
            })
    candidates.sort(
        key=lambda item: (
            item["inliers"], item["inlier_ratio"], -item["reprojection_median"]
        ),
        reverse=True,
    )
    if candidates and candidates[0]["inliers"] >= 12:
        return candidates[0], candidates[:5]

    boundary_candidates = []
    for document_id, document in registry.get("documents", {}).items():
        for page in document.get("pages", []):
            reference = cv2.imread(page["reference_path"], cv2.IMREAD_COLOR)
            if reference is None:
                continue
            alignment = _boundary_homography(photo, reference)
            if alignment is None:
                continue
            boundary_candidates.append({
                **alignment,
                "document_id": document_id,
                "page_index": int(page["page_index"]),
                "reference_path": page["reference_path"],
                "width": int(page["width"]),
                "height": int(page["height"]),
            })
    boundary_candidates.sort(
        key=lambda item: (item["inliers"], item["inlier_ratio"]), reverse=True
    )
    if boundary_candidates:
        return boundary_candidates[0], boundary_candidates[:5]
    return None, []


def _manifest_for_document(registry, document_id):
    candidates = []
    for issue in registry.get("issues", {}).values():
        if issue.get("document_id") != document_id:
            continue
        manifest_path = issue.get("manifest_path")
        if manifest_path and Path(manifest_path).is_file():
            candidates.append((
                str(issue.get("issued_at", "")),
                Path(manifest_path).stat().st_mtime,
                Path(manifest_path),
            ))
    if candidates:
        _, _, selected_path = max(candidates, key=lambda item: (item[0], item[1]))
        return selected_path, _read_json(selected_path)
    raise FileNotFoundError(f"文档{document_id}没有可用发行Manifest")


def trace_document_photo(
    photo_path,
    registry_path,
    key=DEFAULT_KEY,
    output_dir=None,
    enable_document_rerank=True,
    enable_local_partition_sync=True,
    rerank_seed_count=2,
    local_payload_top_k=27,
    trim_ratio=0.20,
):
    started = time.perf_counter()
    photo_path = Path(photo_path).resolve()
    registry = load_document_registry(registry_path)
    photo = cv2.imread(str(photo_path), cv2.IMREAD_COLOR)
    if photo is None:
        raise ValueError(f"无法读取照片: {photo_path}")
    best, page_candidates = match_photo_to_registered_page(photo, registry)
    base_report = {
        "schema_version": TRACE_REPORT_SCHEMA_VERSION,
        "mode": "document_photo",
        "evaluated_at": _now_iso(),
        "photo_path": str(photo_path),
    }
    if best is None:
        report = {
            **base_report,
            "accepted": False,
            "status": "REJECTED_PAGE_ALIGNMENT",
            "alignment": None,
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        }
        return report

    width, height = int(best["width"]), int(best["height"])
    homography = np.asarray(best["homography"], dtype=np.float64)
    rectified = cv2.warpPerspective(
        photo,
        homography,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    source_mask = np.full(photo.shape[:2], 255, dtype=np.uint8)
    valid_mask = cv2.warpPerspective(
        source_mask,
        homography,
        (width, height),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    manifest_path, manifest = _manifest_for_document(registry, best["document_id"])
    if manifest.get("key_id") != _key_id(key):
        raise ValueError("密钥与登记文档的key_id不一致")
    page_key = derive_page_key(
        key, best["document_id"], int(best["page_index"])
    )
    sync_config = manifest["watermark"]["sync_pilot"]
    synchronized, synchronized_mask, synchronization = refine_with_sync_pilot(
        rectified,
        valid_mask,
        page_key,
        int(manifest["watermark"]["block_size"]),
        sync_config,
        scales=np.arange(0.97, 1.0301, 0.005).tolist(),
        min_match_rate=0.57,
        min_normalized_correlation=0.04,
        top_k=12,
    )
    sync_accepted = str(synchronization.get("status", "")).startswith("ACCEPTED")
    best_pilot = synchronization.get("best") or {}
    low_coverage_pilot = bool(
        int(best_pilot.get("observed_bits", 0)) >= 16
        and float(best_pilot.get("match_rate", 0.0)) >= 0.60
        and float(best_pilot.get("normalized_correlation", -1.0)) >= 0.12
    )
    synchronization["low_coverage_candidate_mode"] = {
        "eligible": low_coverage_pilot,
        "thresholds": {
            "min_observed_bits": 16,
            "min_match_rate": 0.60,
            "min_normalized_correlation": 0.12,
        },
    }
    extraction_image = synchronized if sync_accepted else rectified
    extraction_mask = synchronized_mask if sync_accepted else valid_mask
    document_rerank = {
        "enabled": False,
        "status": "DISABLED_OR_SYNC_REJECTED",
    }
    rerank_allowed = bool(sync_accepted or low_coverage_pilot)
    if rerank_allowed and enable_document_rerank:
        (
            extraction_image,
            extraction_mask,
            bits,
            scores,
            details,
            decoded,
            registry_decision,
            document_rerank,
        ) = rerank_document_sync_candidates(
            rectified,
            valid_mask,
            page_key,
            manifest["watermark"],
            registry,
            best["document_id"],
            synchronization,
            trim_ratio=trim_ratio,
            seed_count=rerank_seed_count,
        )
        document_rerank["status"] = (
            "ACCEPTED" if document_rerank.get("accepted") else "COMPLETED"
        )
        document_rerank["low_coverage_seed_mode"] = bool(
            low_coverage_pilot and not sync_accepted
        )
    else:
        bits, scores, details, decoded = _extract_page_scores(
            extraction_image,
            extraction_mask,
            page_key,
            manifest["watermark"],
            trim_ratio=trim_ratio,
        )
        registry_decision = score_registered_tokens(
            scores, registry, document_id=best["document_id"]
        )
    synchronization["document_registry_rerank"] = document_rerank
    local_partition_report = {
        "enabled": False,
        "status": (
            "DISABLED" if not enable_local_partition_sync
            else "SKIPPED_GLOBAL_ACCEPTED"
        ),
    }
    if enable_local_partition_sync and not registry_decision.get("accepted"):
        (
            bits,
            scores,
            details,
            decoded,
            registry_decision,
            local_partition_report,
        ) = local_partition_sync_and_fuse(
            extraction_image,
            extraction_mask,
            page_key,
            manifest["watermark"],
            registry,
            best["document_id"],
            bits,
            scores,
            details,
            decoded,
            registry_decision,
            trim_ratio=trim_ratio,
            payload_top_k=local_payload_top_k,
        )
    synchronization["local_partition_sync"] = local_partition_report
    token = decoded.get("trace_token")
    crc_issue = registry.get("issues", {}).get(token) if decoded.get("crc_pass") else None
    crc_accepted = bool(
        crc_issue
        and crc_issue.get("status", "issued") == "issued"
        and crc_issue.get("document_id") == best["document_id"]
    )
    if crc_accepted:
        accepted = True
        status = "ACCEPTED_CRC_REGISTERED"
        selected_issue = crc_issue
        selected_token = token
        selected_trace_id = crc_issue.get("trace_id")
    elif registry_decision["accepted"]:
        accepted = True
        status = registry_decision["status"]
        selected = registry_decision["selected"]
        selected_issue = selected.get("issue")
        selected_token = selected.get("trace_token")
        selected_trace_id = selected.get("trace_id")
    else:
        accepted = False
        status = registry_decision["status"]
        selected_issue = None
        selected_token = None
        selected_trace_id = None

    alignment_public = {
        key_name: value for key_name, value in best.items() if key_name != "homography"
    }
    alignment_public["homography"] = homography.tolist()
    report = {
        **base_report,
        "accepted": accepted,
        "status": status,
        "document_id": best["document_id"],
        "page_index": int(best["page_index"]),
        "trace_token": selected_token,
        "trace_id": selected_trace_id,
        "issue": selected_issue,
        "alignment": alignment_public,
        "page_candidates": [
            {key_name: value for key_name, value in item.items() if key_name != "homography"}
            for item in page_candidates
        ],
        "synchronization": synchronization,
        "extraction": {
            "crc_pass": bool(decoded.get("crc_pass")),
            "decoded_trace_token": decoded.get("trace_token"),
            "observed_bits": int(sum(bit is not None for bit in bits)),
            "erasures": int(sum(bit is None for bit in bits)),
            "confidence_p50": float(np.median([
                abs(float(score)) for score in scores if score is not None
            ])),
            "aggregation": details.get("aggregation"),
            "details": details,
        },
        "registry_decision": registry_decision,
        "manifest_path": str(manifest_path),
        "elapsed_ms": (time.perf_counter() - started) * 1000.0,
    }
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_dir / "rectified.png"), rectified)
        cv2.imwrite(str(output_dir / "valid_mask.png"), valid_mask)
        cv2.imwrite(str(output_dir / "synchronized.png"), extraction_image)
        cv2.imwrite(str(output_dir / "synchronized_mask.png"), extraction_mask)
        _write_json(output_dir / "trace_report.json", report)
    return report


def _print_embed_result(manifest_path, manifest):
    print(f"水印PDF: {manifest['output_pdf']}")
    print(f"Manifest: {manifest_path}")
    print(f"TraceID: {manifest['trace_id']}")
    print(f"TraceToken: {manifest['trace_token']}")
    print(f"Pages: {manifest['page_count']}")
    print(f"Encoded bits: {len(manifest['encoded_bits'])}")


def build_parser():
    parser = argparse.ArgumentParser(description="PDF文档抗屏摄水印V2.0A.1")
    subparsers = parser.add_subparsers(dest="command", required=True)

    embed = subparsers.add_parser("embed", help="逐页渲染、嵌入并生成水印PDF")
    embed.add_argument("--input", required=True, help="原始PDF")
    embed.add_argument("--registry", default="document_registry.json")
    embed.add_argument("--output")
    embed.add_argument("--assets-root")
    embed.add_argument("--key", default=DEFAULT_KEY)
    embed.add_argument("--dpi", type=int, default=150)
    embed.add_argument("--alpha", type=float, default=42.0)
    embed.add_argument("--repeat", type=int, default=16)
    embed.add_argument("--pilot-bits", type=int, default=64)
    embed.add_argument("--pilot-repeat", type=int, default=6)
    embed.add_argument("--pilot-alpha", type=float, default=78.0)
    embed.add_argument("--poppler-bin")
    embed.add_argument("--recipient")
    embed.add_argument("--session")
    embed.add_argument("--notes")
    embed.add_argument("--trace-token")
    embed.add_argument("--watermark-number")

    digital = subparsers.add_parser(
        "extract-digital", help="从水印PDF的全部页面执行数字提取"
    )
    digital.add_argument("--pdf", required=True)
    digital.add_argument("--manifest", required=True)
    digital.add_argument("--registry", default="document_registry.json")
    digital.add_argument("--key", default=DEFAULT_KEY)
    digital.add_argument("--poppler-bin")
    digital.add_argument("--report")

    photo = subparsers.add_parser(
        "trace-photo", help="无ArUco识别完整页面照片并溯源"
    )
    photo.add_argument("--photo", required=True)
    photo.add_argument("--registry", default="document_registry.json")
    photo.add_argument("--key", default=DEFAULT_KEY)
    photo.add_argument("--output-dir")
    photo.add_argument("--trim-ratio", type=float, default=0.20)
    photo.add_argument("--rerank-seeds", type=int, default=2)
    photo.add_argument("--local-payload-top-k", type=int, default=27)
    photo.add_argument(
        "--no-document-rerank",
        action="store_true",
        help="关闭V2.0A.1文档注册库Top-K亚像素重排",
    )
    photo.add_argument(
        "--no-local-partition-sync",
        action="store_true",
        help="关闭V2.0A.2局部分区同步和软信息融合",
    )
    return parser


def main():
    args = build_parser().parse_args()
    if args.command == "embed":
        manifest_path, manifest = embed_document_pdf(
            args.input,
            args.registry,
            key=args.key,
            output_pdf=args.output,
            assets_root=args.assets_root,
            dpi=args.dpi,
            alpha=args.alpha,
            repeat=args.repeat,
            pilot_bits=args.pilot_bits,
            pilot_repeat=args.pilot_repeat,
            pilot_alpha=args.pilot_alpha,
            poppler_bin=args.poppler_bin,
            recipient=args.recipient,
            session=args.session,
            notes=args.notes,
            trace_token=args.trace_token,
            watermark_number=args.watermark_number,
        )
        _print_embed_result(manifest_path, manifest)
    elif args.command == "extract-digital":
        report = extract_digital_pdf(
            args.pdf,
            args.manifest,
            args.registry,
            key=args.key,
            poppler_bin=args.poppler_bin,
            report_path=args.report,
        )
        print(f"Pages: {report['passed_pages']}/{report['page_count']}")
        print(f"All pages pass: {report['all_pages_pass']}")
        for page in report["pages"]:
            print(
                f"Page {page['page_index']}: CRC={page['crc_pass']} "
                f"Trace={page['trace_match']} Token={page['recovered_trace_token']}"
            )
    elif args.command == "trace-photo":
        report = trace_document_photo(
            args.photo,
            args.registry,
            key=args.key,
            output_dir=args.output_dir,
            enable_document_rerank=not args.no_document_rerank,
            enable_local_partition_sync=not args.no_local_partition_sync,
            rerank_seed_count=args.rerank_seeds,
            local_payload_top_k=args.local_payload_top_k,
            trim_ratio=args.trim_ratio,
        )
        print(f"Status: {report['status']}")
        print(f"Accepted: {report['accepted']}")
        if report.get("accepted"):
            print(f"Document: {report['document_id']}")
            print(f"Page: {report['page_index']}")
            print(f"TraceID: {report['trace_id']}")
            print(f"TraceToken: {report['trace_token']}")


if __name__ == "__main__":
    main()
