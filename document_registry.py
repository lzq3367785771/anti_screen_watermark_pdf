"""Document-oriented TraceToken registry for the PDF V2.0A pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from datetime import datetime
from pathlib import Path

import numpy as np

from codec import (
    bits_to_bytes,
    bytes_to_bits,
    calculate_crc16,
    generate_trace_id,
    hamming_decode,
    hamming_encode,
    hamming_soft_erasure_decode,
)


REGISTRY_SCHEMA_VERSION = 1
DOCUMENT_WATERMARK_VERSION = "pdf-v2.0a"
TRACE_TOKEN_BYTES = 8
PAYLOAD_BYTES = TRACE_TOKEN_BYTES + 2
DOCUMENT_CODEWORD_BITS = PAYLOAD_BYTES * 8 * 7 // 4


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_trace_token(token):
    value = str(token).strip().lower()
    if len(value) != TRACE_TOKEN_BYTES * 2:
        raise ValueError("TraceToken必须为64 bit（16个十六进制字符）")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("TraceToken不是合法十六进制") from exc
    return value


def normalize_watermark_number(number):
    """Normalize a user-facing identifier without changing the carrier token."""

    value = str(number or "").strip().upper()
    if not 3 <= len(value) <= 32:
        raise ValueError("水印号码长度必须为3到32个字符")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", value):
        raise ValueError("水印号码只能包含字母、数字、下划线和短横线")
    return value


def find_issue_by_watermark_number(registry, watermark_number):
    normalized = normalize_watermark_number(watermark_number)
    for token, issue in (registry or {}).get("issues", {}).items():
        existing = issue.get("watermark_number")
        if existing and str(existing).strip().upper() == normalized:
            return token, issue
    return None, None


def empty_document_registry():
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "watermark_version": DOCUMENT_WATERMARK_VERSION,
        "updated_at": _now_iso(),
        "documents": {},
        "issues": {},
    }


def load_document_registry(path):
    path = Path(path)
    if not path.is_file():
        return empty_document_registry()
    with path.open("r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    if int(data.get("schema_version", -1)) != REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"不支持的文档注册库版本: {data.get('schema_version')}")
    data.setdefault("documents", {})
    data.setdefault("issues", {})
    return data


def save_document_registry(path, registry):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    registry = dict(registry)
    registry["schema_version"] = REGISTRY_SCHEMA_VERSION
    registry["watermark_version"] = DOCUMENT_WATERMARK_VERSION
    registry["updated_at"] = _now_iso()
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file_obj:
        json.dump(registry, file_obj, ensure_ascii=False, indent=2)
    temporary.replace(path)
    return registry


def generate_trace_token(registry=None):
    occupied = set((registry or {}).get("issues", {}))
    while True:
        token = secrets.token_hex(TRACE_TOKEN_BYTES)
        if token not in occupied:
            return token


def build_document_payload(token):
    token = normalize_trace_token(token)
    token_bytes = bytes.fromhex(token)
    return token_bytes + calculate_crc16(token_bytes)


def encode_document_token(token):
    return hamming_encode(bytes_to_bits(build_document_payload(token)))


def verify_document_payload(payload):
    if len(payload) != PAYLOAD_BYTES:
        raise ValueError(f"文档Payload必须为{PAYLOAD_BYTES} Byte")
    token_bytes = payload[:TRACE_TOKEN_BYTES]
    received_crc = payload[TRACE_TOKEN_BYTES:]
    calculated_crc = calculate_crc16(token_bytes)
    return {
        "crc_pass": received_crc == calculated_crc,
        "trace_token": token_bytes.hex(),
        "received_crc": received_crc.hex(),
        "calculated_crc": calculated_crc.hex(),
    }


def decode_document_bits(codeword_bits):
    if len(codeword_bits) != DOCUMENT_CODEWORD_BITS:
        raise ValueError(
            f"文档码字必须为{DOCUMENT_CODEWORD_BITS} bit，当前为{len(codeword_bits)}"
        )
    decoded, corrections = hamming_decode([int(bit) for bit in codeword_bits])
    result = verify_document_payload(bits_to_bytes(decoded))
    result["hamming_corrections"] = len(corrections)
    return result


def decode_document_soft_scores(soft_scores):
    if len(soft_scores) != DOCUMENT_CODEWORD_BITS:
        raise ValueError(
            f"文档软信息必须为{DOCUMENT_CODEWORD_BITS}项，当前为{len(soft_scores)}"
        )
    decoded, records = hamming_soft_erasure_decode(soft_scores)
    if any(bit is None for bit in decoded):
        return {
            "crc_pass": False,
            "trace_token": None,
            "status": "ERASURE_DECODE_FAILED",
            "hamming_blocks": records,
        }
    result = verify_document_payload(bits_to_bytes(decoded))
    result["status"] = "CRC_PASS" if result["crc_pass"] else "CRC_FAIL"
    result["hamming_blocks"] = records
    return result


def derive_page_key(base_key, document_id, page_index, purpose="payload"):
    material = (
        f"{DOCUMENT_WATERMARK_VERSION}|{purpose}|{document_id}|"
        f"page={int(page_index)}|{base_key}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def register_document(
    registry_path,
    source_pdf,
    page_records,
    dpi,
    media_box_points,
    assets_dir,
    source_name=None,
):
    source_pdf = Path(source_pdf).resolve()
    document_sha256 = sha256_file(source_pdf)
    document_id = document_sha256[:24]
    registry = load_document_registry(registry_path)
    existing = registry["documents"].get(document_id, {})
    record = {
        **existing,
        "document_id": document_id,
        "document_sha256": document_sha256,
        "source_name": str(source_name or source_pdf.name),
        "source_path": str(source_pdf),
        "page_count": len(page_records),
        "dpi": int(dpi),
        "media_box_points": [float(media_box_points[0]), float(media_box_points[1])],
        "assets_dir": str(Path(assets_dir).resolve()),
        "pages": list(page_records),
        "status": "active",
    }
    record.setdefault("registered_at", _now_iso())
    registry["documents"][document_id] = record
    save_document_registry(registry_path, registry)
    return document_id, record


def issue_document_trace(
    registry_path,
    document_id,
    trace_token=None,
    trace_id=None,
    watermark_number=None,
    metadata=None,
):
    registry = load_document_registry(registry_path)
    if document_id not in registry["documents"]:
        raise KeyError(f"文档未登记: {document_id}")
    token = normalize_trace_token(trace_token) if trace_token else generate_trace_token(registry)
    if token in registry["issues"]:
        raise ValueError(f"TraceToken已存在: {token}")
    metadata = dict(metadata) if metadata else {}
    requested_number = (
        watermark_number
        if watermark_number is not None
        else metadata.get("watermark_number")
    )
    normalized_number = None
    if requested_number is not None:
        normalized_number = normalize_watermark_number(requested_number)
        existing_token, _ = find_issue_by_watermark_number(
            registry, normalized_number
        )
        if existing_token is not None:
            raise ValueError(f"水印号码已存在: {normalized_number}")
    trace_id = trace_id or generate_trace_id()
    if len(trace_id) != 32:
        raise ValueError("TraceID必须为128 bit（32个十六进制字符）")
    bytes.fromhex(trace_id)
    record = {
        **metadata,
        "trace_token": token,
        "trace_id": trace_id.lower(),
        "document_id": document_id,
        "status": "issued",
        "issued_at": _now_iso(),
        "watermark_version": DOCUMENT_WATERMARK_VERSION,
    }
    if normalized_number is not None:
        record["watermark_number"] = normalized_number
    registry["issues"][token] = record
    save_document_registry(registry_path, registry)
    return record


def attach_issue_artifact(registry_path, trace_token, output_pdf, manifest_path):
    token = normalize_trace_token(trace_token)
    registry = load_document_registry(registry_path)
    if token not in registry["issues"]:
        raise KeyError(f"TraceToken未登记: {token}")
    registry["issues"][token].update({
        "output_pdf": str(Path(output_pdf).resolve()),
        "output_sha256": sha256_file(output_pdf),
        "manifest_path": str(Path(manifest_path).resolve()),
    })
    save_document_registry(registry_path, registry)
    return registry["issues"][token]


def retire_document_issue(registry_path, trace_token, trash_path=None):
    """Disable one issued watermark while retaining a minimal audit record."""

    token = normalize_trace_token(trace_token)
    registry = load_document_registry(registry_path)
    issue = registry["issues"].get(token)
    if issue is None:
        raise KeyError(f"水印记录不存在: {token}")
    if issue.get("status", "issued") != "issued":
        raise ValueError("该水印已经删除或停用")
    issue["status"] = "deleted"
    issue["deleted_at"] = _now_iso()
    if trash_path is not None:
        issue["trash_path"] = str(Path(trash_path).resolve())
    save_document_registry(registry_path, registry)
    return dict(issue)


def score_registered_tokens(
    soft_scores,
    registry,
    document_id=None,
    min_observed_ratio=0.80,
    min_normalized_score=0.18,
    min_z_score=4.0,
    min_margin_z=1.0,
    min_hard_match_rate=0.60,
):
    if len(soft_scores) != DOCUMENT_CODEWORD_BITS:
        raise ValueError("文档软信息长度不正确")
    observed = np.asarray([
        value is not None and math.isfinite(float(value)) for value in soft_scores
    ], dtype=bool)
    observed_count = int(np.sum(observed))
    values = np.asarray([
        float(value) if keep else 0.0 for value, keep in zip(soft_scores, observed)
    ], dtype=np.float32)
    l1 = float(np.sum(np.abs(values[observed])))
    l2 = float(np.sqrt(np.sum(np.square(values[observed]))))
    scores = []
    if observed_count:
        signs = values[observed] > 0
        for token, issue in registry.get("issues", {}).items():
            if issue.get("status", "issued") != "issued":
                continue
            if document_id and issue.get("document_id") != document_id:
                continue
            expected = np.asarray(encode_document_token(token), dtype=np.float32)
            expected = expected[observed] * 2.0 - 1.0
            raw = float(expected @ values[observed])
            hard = float(np.mean((expected > 0) == signs))
            scores.append({
                "trace_token": token,
                "trace_id": issue.get("trace_id"),
                "document_id": issue.get("document_id"),
                "observed_bits": observed_count,
                "raw_score": raw,
                "normalized_score": raw / max(l1, 1e-12),
                "z_score": raw / max(l2, 1e-12),
                "hard_match_rate": hard,
                "issue": issue,
            })
    scores.sort(
        key=lambda item: (
            item["z_score"], item["normalized_score"], item["hard_match_rate"]
        ),
        reverse=True,
    )
    thresholds = {
        "min_observed_bits": int(math.ceil(DOCUMENT_CODEWORD_BITS * min_observed_ratio)),
        "min_normalized_score": float(min_normalized_score),
        "min_z_score": float(min_z_score),
        "min_margin_z": float(min_margin_z),
        "min_hard_match_rate": float(min_hard_match_rate),
    }
    if not scores:
        return {
            "accepted": False,
            "status": "REJECTED_EMPTY_CANDIDATES",
            "selected": None,
            "top_candidates": [],
            "thresholds": thresholds,
        }
    best = scores[0]
    runner_up_z = scores[1]["z_score"] if len(scores) > 1 else 0.0
    margin_z = float(best["z_score"] - runner_up_z)
    selected = {**best, "margin_z": margin_z}
    failures = []
    if observed_count < thresholds["min_observed_bits"]:
        failures.append("INSUFFICIENT_OBSERVED_BITS")
    if best["normalized_score"] < min_normalized_score:
        failures.append("LOW_NORMALIZED_SCORE")
    if best["z_score"] < min_z_score:
        failures.append("LOW_Z_SCORE")
    if margin_z < min_margin_z:
        failures.append("LOW_MARGIN")
    if best["hard_match_rate"] < min_hard_match_rate:
        failures.append("LOW_HARD_MATCH_RATE")
    return {
        "accepted": not failures,
        "status": "ACCEPTED_REGISTRY_ML" if not failures else "REJECTED_" + "_".join(failures),
        "selected": selected,
        "top_candidates": scores[:5],
        "thresholds": thresholds,
        "rejection_reasons": failures,
    }
