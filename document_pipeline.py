"""Unified document watermark issuing entry for V2.1.0.

This module is the format-dispatch layer above the existing PDF pipeline.

Current status:
    PDF   -> implemented
    DOCX  -> recognized, renderer not implemented yet
    PPTX  -> recognized, renderer not implemented yet
    XLSX  -> recognized, renderer not implemented yet

The watermark carrier itself remains in the existing PDF/image pipeline.
"""

from __future__ import annotations

from pathlib import Path

from document_registry import infer_source_type
from pdf_pipeline import DEFAULT_KEY, embed_document_pdf


DOCUMENT_PIPELINE_VERSION = "v2.1.0"

SUPPORTED_SOURCE_TYPES = {
    "pdf",
    "docx",
    "pptx",
    "xlsx",
}

IMPLEMENTED_EMBED_TYPES = {
    "pdf",
}


def detect_document_type(source_path, source_type=None):
    """识别通用文档类型，并拒绝当前系统不支持的扩展名。"""

    resolved = infer_source_type(
        source_path,
        source_type=source_type,
    )

    if resolved not in SUPPORTED_SOURCE_TYPES:
        supported = ", ".join(
            sorted(SUPPORTED_SOURCE_TYPES)
        )

        raise ValueError(
            f"不支持的文档类型: {resolved}；"
            f"当前支持识别: {supported}"
        )

    return resolved


def embed_document(
    input_path,
    registry_path,
    key=DEFAULT_KEY,
    output_path=None,
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
    source_type=None,
):
    """统一文档水印发行入口。

    V2.1.0 当前只真正实现 PDF Adapter。

    DOCX / PPTX / XLSX 已能够被识别，但在对应 Office Renderer
    完成之前会明确返回 NotImplementedError，而不会错误地进入
    PDF 处理路径。
    """

    input_path = Path(input_path).resolve()

    if not input_path.is_file():
        raise FileNotFoundError(
            f"源文档不存在: {input_path}"
        )

    resolved_type = detect_document_type(
        input_path,
        source_type=source_type,
    )

    # --------------------------------------------------------
    # PDF Adapter
    # --------------------------------------------------------

    if resolved_type == "pdf":

        return embed_document_pdf(
            input_pdf=input_path,
            registry_path=registry_path,
            key=key,
            output_pdf=output_path,
            assets_root=assets_root,
            dpi=dpi,
            alpha=alpha,
            repeat=repeat,
            pilot_bits=pilot_bits,
            pilot_repeat=pilot_repeat,
            pilot_alpha=pilot_alpha,
            poppler_bin=poppler_bin,
            recipient=recipient,
            session=session,
            notes=notes,
            trace_token=trace_token,
            watermark_number=watermark_number,
            source_name=source_name,
        )

    # --------------------------------------------------------
    # Office Adapter
    #
    # 先识别但不假装已经支持。
    # 后续 V2.1.1 / V2.1.2 会逐个接入。
    # --------------------------------------------------------

    raise NotImplementedError(
        f"{resolved_type.upper()} 文件已经被 V2.1.0 "
        f"识别，但对应的文档渲染/重建 Adapter 尚未实现"
    )