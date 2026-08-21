"""Format-independent rendered-page watermark carrier for V2.1.0.

This module operates only on rendered page images.

It does not know how PDF, DOCX, PPTX or XLSX files are rendered or rebuilt.
Its responsibility is:

    rendered reference pages
        -> document registration
        -> TraceToken issuance
        -> payload encoding
        -> adaptive DCT payload embedding
        -> PN pilot embedding
        -> watermarked page images
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from document_registry import (
    DOCUMENT_CODEWORD_BITS,
    derive_page_key,
    encode_document_token,
    issue_document_trace,
    register_document,
    sha256_file,
    validate_key_id,
)
from synchronization import generate_sync_pilot
from watermark import (
    embed_bit,
    generate_positions,
)


def build_watermark_config(
    alpha=42.0,
    repeat=16,
    block_size=8,
    pilot_bits=64,
    pilot_repeat=6,
    pilot_alpha=78.0,
    payload_flat_bright_scale=0.60,
    pilot_flat_bright_scale=0.60,
):
    """Build the canonical carrier configuration written to every manifest."""

    block_size = int(block_size)
    repeat = int(repeat)
    pilot_bits = int(pilot_bits)
    pilot_repeat = int(pilot_repeat)
    alpha = float(alpha)
    pilot_alpha = float(pilot_alpha)
    payload_flat_bright_scale = float(payload_flat_bright_scale)
    pilot_flat_bright_scale = float(pilot_flat_bright_scale)

    if block_size < 2:
        raise ValueError("block_size必须至少为2")
    if repeat < 1 or pilot_repeat < 1:
        raise ValueError("payload/pilot重复次数必须为正整数")
    if pilot_bits < 16:
        raise ValueError("pilot bit_count必须至少为16")
    if alpha <= 0.0 or pilot_alpha <= 0.0:
        raise ValueError("payload/pilot alpha必须为正数")
    if payload_flat_bright_scale <= 0.0 or pilot_flat_bright_scale <= 0.0:
        raise ValueError("自适应alpha缩放系数必须为正数")

    return {
        "block_size": block_size,
        "bit_count": DOCUMENT_CODEWORD_BITS,
        "alpha": alpha,
        "repeat": repeat,
        "adaptive_alpha": True,
        "flat_bright_scale": payload_flat_bright_scale,
        "sync_pilot": {
            "enabled": True,
            "version": "pn64-v1",
            "bit_count": pilot_bits,
            "repeat": pilot_repeat,
            "alpha": pilot_alpha,
            "adaptive_alpha": True,
            "flat_bright_scale": pilot_flat_bright_scale,
            "position_offset": DOCUMENT_CODEWORD_BITS * repeat,
        },
    }


def _consistent_repeat_from_stats(pages, section, bit_count):
    inferred = set()
    for page in pages or []:
        stats = page.get(section) if isinstance(page, dict) else None
        if not isinstance(stats, dict):
            continue
        units = stats.get("dct_units")
        if units is None:
            continue
        units = int(units)
        if units > 0 and units % int(bit_count) == 0:
            inferred.add(units // int(bit_count))
    return inferred.pop() if len(inferred) == 1 else None


def normalize_watermark_config(config, pages=None, strict=False):
    """Normalize schema-v1 manifests and validate schema-v2 carrier fields.

    Early DOCX manifests omitted the pilot enable flag and offset and recorded a
    payload repeat that disagreed with the actual DCT unit count.  Page embedding
    statistics are authoritative for those legacy files.
    """

    if not isinstance(config, dict):
        raise ValueError("Manifest缺少watermark配置")

    raw = dict(config)
    sync_raw = dict(raw.get("sync_pilot") or {})
    bit_count = int(raw.get("bit_count", DOCUMENT_CODEWORD_BITS))
    if bit_count != DOCUMENT_CODEWORD_BITS:
        raise ValueError(
            "Manifest payload bit_count不匹配: "
            f"{bit_count} != {DOCUMENT_CODEWORD_BITS}"
        )

    repeat = int(raw.get("repeat", 0) or 0)
    inferred_repeat = _consistent_repeat_from_stats(
        pages, "payload_embedding", bit_count
    )
    if inferred_repeat is not None and inferred_repeat != repeat:
        if strict:
            raise ValueError("Manifest payload repeat与页面DCT统计不一致")
        repeat = inferred_repeat
    if repeat < 1:
        raise ValueError("Manifest缺少有效payload repeat")

    pilot_bits = int(
        sync_raw.get("bit_count", sync_raw.get("bits", 64))
    )
    pilot_repeat = int(sync_raw.get("repeat", 0) or 0)
    inferred_pilot_repeat = _consistent_repeat_from_stats(
        pages, "pilot_embedding", pilot_bits
    )
    if inferred_pilot_repeat is not None and inferred_pilot_repeat != pilot_repeat:
        if strict:
            raise ValueError("Manifest pilot repeat与页面DCT统计不一致")
        pilot_repeat = inferred_pilot_repeat
    if pilot_bits < 1 or pilot_repeat < 1:
        raise ValueError("Manifest缺少有效pilot bit_count/repeat")

    normalized = build_watermark_config(
        alpha=float(raw.get("alpha", 42.0)),
        repeat=repeat,
        block_size=int(raw.get("block_size", 8)),
        pilot_bits=pilot_bits,
        pilot_repeat=pilot_repeat,
        pilot_alpha=float(sync_raw.get("alpha", 78.0)),
        payload_flat_bright_scale=float(raw.get("flat_bright_scale", 0.60)),
        pilot_flat_bright_scale=float(
            sync_raw.get("flat_bright_scale", 0.60)
        ),
    )

    explicit_offset = sync_raw.get("position_offset")
    if explicit_offset is not None:
        explicit_offset = int(explicit_offset)
        if explicit_offset != normalized["sync_pilot"]["position_offset"]:
            raise ValueError(
                "Manifest pilot position_offset与payload配置不一致"
            )
    return normalized


def _adaptive_alpha(
    block,
    base_alpha,
    flat_bright_scale=0.60,
):
    mean = float(np.mean(block))
    deviation = float(np.std(block))

    if mean >= 247.0 and deviation <= 2.5:
        scale = float(flat_bright_scale)

    elif deviation < 7.0:
        scale = 0.78

    elif deviation > 35.0:
        scale = 1.12

    else:
        scale = 1.0

    return max(
        12.0,
        float(base_alpha) * scale,
    )


def embed_bits_adaptive(
    image,
    bits,
    key,
    alpha,
    repeat,
    block_size=8,
    position_offset=0,
    flat_bright_scale=0.60,
):
    """在渲染页面中自适应嵌入一组bit。

    该函数与具体文档格式无关。

    PDF页面、Word页面、PowerPoint Slide和Excel打印页，
    只要已经转换成OpenCV图像，就可以使用同一套DCT嵌入。
    """

    if image is None:
        raise ValueError(
            "输入页面为空"
        )

    height, width = image.shape[:2]

    total_count = (
        int(position_offset)
        + len(bits) * int(repeat)
    )

    positions = generate_positions(
        height,
        width,
        key,
        total_count,
        block_size=int(block_size),
    )

    ycrcb = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2YCrCb,
    )

    y, cr, cb = cv2.split(ycrcb)

    index = int(position_offset)

    alpha_values = []

    for bit in bits:

        for _ in range(int(repeat)):

            row, col = positions[index]

            index += 1

            y1 = row * int(block_size)
            x1 = col * int(block_size)

            block = y[
                y1:y1 + int(block_size),
                x1:x1 + int(block_size),
            ]

            local_alpha = _adaptive_alpha(
                block,
                alpha,
                flat_bright_scale=
                    flat_bright_scale,
            )

            alpha_values.append(
                local_alpha
            )

            y[
                y1:y1 + int(block_size),
                x1:x1 + int(block_size),
            ] = embed_bit(
                block,
                int(bit),
                alpha=local_alpha,
            )

    result = cv2.cvtColor(
        cv2.merge([y, cr, cb]),
        cv2.COLOR_YCrCb2BGR,
    )

    return result, {
        "alpha_min": float(
            min(alpha_values)
        ),
        "alpha_max": float(
            max(alpha_values)
        ),
        "alpha_mean": float(
            np.mean(alpha_values)
        ),
        "dct_units": len(
            alpha_values
        ),
    }


def issue_watermarked_pages(
    source_path,
    registry_path,
    page_records,
    dpi,
    media_box_points,
    document_assets,
    key,
    key_id,
    alpha=42.0,
    repeat=16,
    pilot_bits=64,
    pilot_repeat=6,
    pilot_alpha=78.0,
    payload_flat_bright_scale=0.60,
    pilot_flat_bright_scale=0.60,
    recipient=None,
    session=None,
    notes=None,
    trace_token=None,
    watermark_number=None,
    source_name=None,
    source_type=None,
    render_unit_type=None,
    issue_context=None
):
    """对已经渲染好的页面执行统一水印发行。

    Parameters
    ----------
    source_path:
        原始文档路径，可以是PDF、DOCX、PPTX或XLSX。

    page_records:
        已经持久化保存的参考页面记录。
        每条至少包含：
            page_index
            width
            height
            reference_path
            reference_sha256

        document_assets:
        当前document_id对应的资源目录。

    issue_context:
        可选的发行事务上下文字典。

        如果提供，函数会在 issue_document_trace()
        成功创建 Registry issue 后立即写入：
            issue_created
            trace_token
            document_id
            issue

        在 issue 页面目录处理阶段还会写入：
            issue_page_dir
            issue_page_dir_created

        只有当前调用成功创建了新的 token 专属目录时，
        issue_page_dir_created 才为 True。

        即使后续页面嵌入失败且函数没有正常返回，
        上层 Adapter 仍可利用这些信息安全回滚
        本次尚未 attach artifact 的 issue。

    Returns
    -------
    dict
        返回document、issue、payload、水印页面等结果。
        上层PDF/PPTX/DOCX Adapter负责把这些页面重新组成最终文件。
    """

    key_id = validate_key_id(
        key,
        key_id,
    )
    watermark_config = build_watermark_config(
        alpha=alpha,
        repeat=repeat,
        block_size=8,
        pilot_bits=pilot_bits,
        pilot_repeat=pilot_repeat,
        pilot_alpha=pilot_alpha,
        payload_flat_bright_scale=payload_flat_bright_scale,
        pilot_flat_bright_scale=pilot_flat_bright_scale,
    )
    alpha = watermark_config["alpha"]
    repeat = watermark_config["repeat"]
    pilot_bits = watermark_config["sync_pilot"]["bit_count"]
    pilot_repeat = watermark_config["sync_pilot"]["repeat"]
    pilot_alpha = watermark_config["sync_pilot"]["alpha"]
    payload_flat_bright_scale = watermark_config["flat_bright_scale"]
    pilot_flat_bright_scale = watermark_config["sync_pilot"][
        "flat_bright_scale"
    ]

    source_path = Path(
        source_path
    ).resolve()

    registry_path = Path(
        registry_path
    ).resolve()

    document_assets = Path(
        document_assets
    ).resolve()

    # --------------------------------------------------------
    # 本次发行的事务上下文
    #
    # 上层 Adapter 可以传入一个 dict。
    # 一旦 issue_document_trace() 真正成功，
    # Carrier 会立即把本次创建的 TraceToken 写入该字典。
    #
    # 因此即使后续页面嵌入发生异常、函数没有 return，
    # 上层仍然能够准确知道应该回滚哪个 issue。
    # --------------------------------------------------------

    if issue_context is not None:

        if not isinstance(
            issue_context,
            dict,
        ):
            raise TypeError(
                "issue_context必须为dict或None"
            )

        # 防止调用者错误复用旧 context，
        # 导致一次早期失败误回滚前一次发行。
        issue_context.clear()



    if not source_path.is_file():
        raise FileNotFoundError(
            f"源文档不存在: {source_path}"
        )

    if not page_records:
        raise ValueError(
            "没有可用于水印发行的渲染页面"
        )

    # --------------------------------------------------------
    # 1. 注册源文档
    # --------------------------------------------------------

    document_id, document = register_document(
        registry_path,
        source_path,
        page_records,
        dpi,
        media_box_points,
        document_assets,
        source_name=source_name,
        source_type=source_type,
        render_unit_type=render_unit_type,
    )

    # --------------------------------------------------------
    # 2. 为本次发行创建独立TraceToken / TraceID
    # --------------------------------------------------------

    issue = issue_document_trace(
        registry_path,
        document_id,
        trace_token=trace_token,
        watermark_number=watermark_number,
        metadata={
            "recipient": recipient,
            "session": session,
            "notes": notes,
            "key_id": key_id,
        },
    )

    token = issue[
        "trace_token"
    ]

    # --------------------------------------------------------
    # issue_document_trace() 已经成功返回。
    #
    # 到这一刻才能确认：
    # 当前 TraceToken 确实由本次调用创建。
    #
    # 只有此时才把所有权写入 issue_context。
    # --------------------------------------------------------

    if issue_context is not None:

        issue_context.update({
            "issue_created": True,
            "trace_token": token,
            "document_id": document_id,
            "issue": dict(issue),
        })



    # --------------------------------------------------------
    # 3. 64 bit TraceToken + CRC16 + Hamming -> 140 bit
    # --------------------------------------------------------

    payload = encode_document_token(
        token
    )

    if len(payload) != DOCUMENT_CODEWORD_BITS:
        raise RuntimeError(
            "文档载荷编码长度异常: "
            f"{len(payload)} != "
            f"{DOCUMENT_CODEWORD_BITS}"
        )

    # --------------------------------------------------------
        # --------------------------------------------------------
    # 4. 当前发行版本的水印页面保存位置
    # --------------------------------------------------------

    issue_page_dir = (
        document_assets
        / "issues"
        / token
    )

    if issue_context is not None:
        issue_context[
            "issue_page_dir"
        ] = issue_page_dir

        # 在真正创建目录之前先明确标记：
        # 当前调用尚未拥有这个目录。
        issue_context[
            "issue_page_dir_created"
        ] = False

    # --------------------------------------------------------
    # 一个 TraceToken 的 issue 目录必须由本次发行独占。
    #
    # 如果目录已经存在，它可能是历史失败发行留下的
    # 孤儿目录，因此不能继续覆盖，也不能在回滚时误删。
    # --------------------------------------------------------

    if issue_page_dir.exists():
        raise FileExistsError(
            "本次发行的issue资源目录已存在: "
            f"{issue_page_dir}"
        )

    issue_page_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    if issue_context is not None:
        issue_context[
            "issue_page_dir_created"
        ] = True

    embedded_pages = []
    manifest_pages = []

    # --------------------------------------------------------
    # 5. 每一个渲染页面使用完全相同的Carrier逻辑
    # --------------------------------------------------------

    for page_record in page_records:

        page_index = int(
            page_record["page_index"]
        )

        reference_path = Path(
            page_record["reference_path"]
        )

        image = cv2.imread(
            str(reference_path),
            cv2.IMREAD_COLOR,
        )

        if image is None:
            raise RuntimeError(
                f"无法读取参考页面: "
                f"{reference_path}"
            )

        # 每页独立派生密钥。
        page_key = derive_page_key(
            key,
            document_id,
            page_index,
        )

        # ----------------------------------------------------
        # Payload
        # ----------------------------------------------------

        watermarked, payload_stats = (
            embed_bits_adaptive(
                image,
                payload,
                page_key,
                alpha=alpha,
                repeat=repeat,
                block_size=8,
                position_offset=0,
                flat_bright_scale=
                    payload_flat_bright_scale,
            )
        )

        # ----------------------------------------------------
        # PN Pilot
        # ----------------------------------------------------

        pilot = generate_sync_pilot(
            page_key,
            int(pilot_bits),
        )

        watermarked, pilot_stats = (
            embed_bits_adaptive(
                watermarked,
                pilot,
                page_key,
                alpha=pilot_alpha,
                repeat=pilot_repeat,
                block_size=8,
                position_offset=(
                    DOCUMENT_CODEWORD_BITS
                    * int(repeat)
                ),
                flat_bright_scale=
                    pilot_flat_bright_scale,
            )
        )

        # ----------------------------------------------------
        # 保存当前水印页面
        # ----------------------------------------------------

        target = (
            issue_page_dir
            / (
                f"page_{page_index:03d}"
                "_watermarked.png"
            )
        )

        if not cv2.imwrite(
            str(target),
            watermarked,
        ):
            raise RuntimeError(
                f"无法保存水印页面: {target}"
            )

        embedded_pages.append(
            target
        )

        manifest_pages.append({
            **page_record,

            "watermarked_page_path":
                str(target.resolve()),

            "watermarked_page_sha256":
                sha256_file(target),

            "payload_embedding":
                payload_stats,

            "pilot_embedding":
                pilot_stats,
        })

    # --------------------------------------------------------
    # 6. 不在这里生成PDF/PPTX/DOCX/XLSX
    # --------------------------------------------------------

    return {
        "document_id": document_id,

        "document": document,

        "issue": issue,

        "trace_id": issue[
            "trace_id"
        ],

        "trace_token": token,

        "watermark_number":
            issue.get(
                "watermark_number"
            ),

        "encoded_bits": [
            int(bit)
            for bit in payload
        ],

        "embedded_pages":
            embedded_pages,

        "manifest_pages":
            manifest_pages,

        "watermark_config":
            watermark_config,

        "issue_page_dir":
            issue_page_dir,
    }
