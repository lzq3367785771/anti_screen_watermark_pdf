"""
DOCX document watermark pipeline.

Flow:

DOCX
 |
 | Microsoft Word COM rendering
 v
page PNG images
 |
 | document_carrier
 v
watermarked page PNG images
 |
 | raster PDF rebuild
 v
watermarked PDF
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


import cv2


from office_renderer import render_docx_pages

from document_carrier import (
    issue_watermarked_pages,
)

from document_registry import (
    DOCUMENT_MANIFEST_SCHEMA_VERSION,
    DOCUMENT_WATERMARK_VERSION,
    attach_issue_artifact,
    load_registered_reference_set,
    sha256_file,
    validate_key_id,
)

from pdf_pipeline import (
    DEFAULT_KEY,
    _rollback_pdf_issue_failure,
    _save_raster_pdf,
)


MANIFEST_SCHEMA_VERSION = DOCUMENT_MANIFEST_SCHEMA_VERSION


def _now_iso():
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as file_obj:
        json.dump(
            data,
            file_obj,
            ensure_ascii=False,
            indent=2,
        )

    temporary.replace(path)



def build_page_records(
    pages,
):
    """
    Build Canonical Reference page records.

    Compatible with document_carrier.
    """

    records = []

    for index, page in enumerate(
        pages,
        start=1,
    ):

        image = cv2.imread(
            str(page),
            cv2.IMREAD_COLOR,
        )

        if image is None:
            raise RuntimeError(
                f"无法读取渲染页面: {page}"
            )

        height, width = image.shape[:2]

        records.append(
            {
                "page_index": index,

                "width": int(width),

                "height": int(height),

                "reference_path":
                    str(
                        Path(page).resolve()
                    ),

                "reference_sha256":
                    sha256_file(page),
            }
        )

    return records


def _prepare_docx_reference_set(
    input_docx,
    registry_path,
    document_assets,
    document_id,
    dpi,
):
    """Reuse a verified DOCX reference set or publish a staged render."""

    document_assets = Path(document_assets).resolve()
    reference_dir = document_assets / "reference_pages"
    registered = load_registered_reference_set(
        registry_path,
        document_id,
        expected_source_type="docx",
        expected_dpi=dpi,
    )
    if registered is not None:
        media_box = registered.get("media_box_points")
        if not isinstance(media_box, (list, tuple)) or len(media_box) != 2:
            raise ValueError("已登记DOCX缺少有效media_box_points")
        pages = [dict(page) for page in registered["pages"]]
        return {
            "reused": True,
            "page_records": pages,
            "media_box_points": [float(media_box[0]), float(media_box[1])],
            "render_info": {
                "source_type": "docx",
                "render_unit_type": "page",
                "renderer": "canonical_reference_reuse",
                "dpi": int(dpi),
                "page_count": len(pages),
                "page_sizes": [
                    {
                        "page_index": int(page.get("page_index", index)),
                        "width": int(page["width"]),
                        "height": int(page["height"]),
                    }
                    for index, page in enumerate(pages, start=1)
                ],
            },
        }

    if reference_dir.exists():
        raise FileExistsError(
            "reference_pages已存在，但Registry中没有可验证的DOCX "
            f"Canonical Reference: {reference_dir}"
        )

    document_assets.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(
        prefix=".reference_pages_staging_",
        dir=str(document_assets),
    ))
    published = False
    try:
        pages, render_info = render_docx_pages(
            input_docx,
            staging_dir,
            dpi=dpi,
        )
        if not pages or not isinstance(render_info, dict):
            raise ValueError("DOCX渲染器返回了空页面或非法render_info")
        if int(render_info.get("page_count", -1)) != len(pages):
            raise ValueError("DOCX page_count与实际渲染页面数量不一致")

        staged_records = []
        staging_root = staging_dir.resolve()
        for index, page_path in enumerate(pages, start=1):
            page_path = Path(page_path).resolve()
            if page_path.parent != staging_root:
                raise ValueError(f"DOCX渲染页面不在staging目录内: {page_path}")
            image = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError(f"无法读取DOCX渲染页面: {page_path}")
            height, width = image.shape[:2]
            staged_records.append({
                "page_index": index,
                "unit_type": "page",
                "width": int(width),
                "height": int(height),
                "filename": page_path.name,
                "reference_sha256": sha256_file(page_path),
            })

        page_records = [
            {
                "page_index": record["page_index"],
                "unit_type": "page",
                "width": record["width"],
                "height": record["height"],
                "reference_path": str(
                    (reference_dir / record["filename"]).resolve()
                ),
                "reference_sha256": record["reference_sha256"],
            }
            for record in staged_records
        ]
        first = staged_records[0]
        media_box = [
            float(first["width"]) * 72.0 / float(dpi),
            float(first["height"]) * 72.0 / float(dpi),
        ]
        if reference_dir.exists():
            raise FileExistsError(f"DOCX reference_pages被并发创建: {reference_dir}")
        staging_dir.replace(reference_dir)
        published = True
        return {
            "reused": False,
            "page_records": page_records,
            "media_box_points": media_box,
            "render_info": dict(render_info),
        }
    finally:
        if not published and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)






def embed_docx(
    input_docx,
    registry_path,
    output_pdf,
    key=DEFAULT_KEY,
    key_id=None,
    dpi=150,
    assets_root=None,
    alpha=42.0,
    repeat=16,
    pilot_bits=64,
    pilot_repeat=6,
    pilot_alpha=78.0,
    recipient=None,
    session=None,
    notes=None,
    trace_token=None,
    watermark_number=None,
    source_name=None,
):
    """Issue a DOCX watermark as a flattened raster PDF transaction."""

    input_docx = Path(input_docx).resolve()
    registry_path = Path(registry_path).resolve()
    output_pdf = Path(output_pdf).resolve()
    if not input_docx.is_file():
        raise FileNotFoundError(f"DOCX不存在: {input_docx}")
    if input_docx.suffix.lower() != ".docx":
        raise ValueError(f"输入文件不是DOCX: {input_docx}")
    if output_pdf.suffix.lower() != ".pdf":
        raise ValueError("DOCX水印输出必须为.pdf")
    if int(dpi) <= 0:
        raise ValueError("dpi必须为正整数")

    resolved_key_id = validate_key_id(key, key_id)
    source_sha256 = sha256_file(input_docx)
    provisional_document_id = source_sha256[:24]
    assets_root = (
        Path(assets_root).resolve()
        if assets_root is not None
        else (input_docx.parent / ".document_assets").resolve()
    )
    document_assets = assets_root / provisional_document_id
    reference = _prepare_docx_reference_set(
        input_docx=input_docx,
        registry_path=registry_path,
        document_assets=document_assets,
        document_id=provisional_document_id,
        dpi=int(dpi),
    )

    issue_context = {}
    output_pdf_owned = False
    output_tmp_owned = False
    output_backup = None
    manifest_path = None
    manifest_owned = False
    committed = False

    try:
        result = issue_watermarked_pages(
            source_path=input_docx,
            registry_path=registry_path,
            page_records=reference["page_records"],
            dpi=int(dpi),
            media_box_points=reference["media_box_points"],
            document_assets=document_assets,
            key=key,
            key_id=resolved_key_id,
            alpha=alpha,
            repeat=repeat,
            pilot_bits=pilot_bits,
            pilot_repeat=pilot_repeat,
            pilot_alpha=pilot_alpha,
            recipient=recipient,
            session=session,
            notes=notes,
            trace_token=trace_token,
            watermark_number=watermark_number,
            source_name=source_name or input_docx.name,
            source_type="docx",
            render_unit_type="page",
            issue_context=issue_context,
        )
        token = result["trace_token"]
        output_tmp = output_pdf.with_suffix(".tmp.pdf")
        if output_tmp.exists():
            raise FileExistsError(
                f"DOCX PDF临时输出已存在，拒绝覆盖: {output_tmp}"
            )
        output_tmp_owned = True

        if output_pdf.exists():
            if not output_pdf.is_file():
                raise FileExistsError(f"输出路径不是文件: {output_pdf}")
            output_backup = output_pdf.with_name(
                output_pdf.name + "." + token + ".rollback.bak"
            )
            if output_backup.exists():
                raise FileExistsError(f"回滚备份已存在: {output_backup}")
            output_pdf.replace(output_backup)
        output_pdf_owned = True
        _save_raster_pdf(result["embedded_pages"], output_pdf, dpi=int(dpi))

        manifest_path = (
            document_assets / f"manifest_{token}.json"
        ).resolve()
        manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        if manifest_path.exists() or manifest_tmp.exists():
            raise FileExistsError(
                f"DOCX Manifest或临时文件已存在: {manifest_path}"
            )
        manifest_owned = True
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "watermark_version": DOCUMENT_WATERMARK_VERSION,
            "source_docx": str(input_docx),
            "source_sha256": source_sha256,
            "source_type": "docx",
            "render_unit_type": "page",
            "output_pdf": str(output_pdf),
            "output_sha256": sha256_file(output_pdf),
            "document_id": result["document_id"],
            "trace_id": result["trace_id"],
            "trace_token": token,
            "watermark_number": result.get("watermark_number"),
            "encoded_bits": result["encoded_bits"],
            "page_count": len(reference["page_records"]),
            "dpi": int(dpi),
            "media_box_points": reference["media_box_points"],
            "key_id": resolved_key_id,
            "watermark": result["watermark_config"],
            "render_info": reference["render_info"],
            "reference_reused": bool(reference["reused"]),
            "pages": result["manifest_pages"],
        }
        _write_json(manifest_path, manifest)
        attach_issue_artifact(
            registry_path,
            token,
            output_pdf,
            manifest_path,
        )
        committed = True
        if output_backup is not None and output_backup.is_file():
            try:
                output_backup.unlink()
            except OSError:
                pass
        return {
            **result,
            "manifest_path": manifest_path,
            "manifest": manifest,
        }
    except Exception as exc:
        if committed:
            raise
        try:
            diagnostics = _rollback_pdf_issue_failure(
                registry_path=registry_path,
                issue_context=issue_context,
                output_pdf=output_pdf,
                output_pdf_owned=output_pdf_owned,
                output_tmp_owned=output_tmp_owned,
                output_backup=output_backup,
                manifest_path=manifest_path,
                manifest_owned=manifest_owned,
            )
        except Exception as rollback_exc:
            diagnostics = [
                "DOCX回滚执行器异常: "
                f"{type(rollback_exc).__name__}: {rollback_exc}"
            ]
        if diagnostics:
            try:
                setattr(exc, "rollback_diagnostics", diagnostics)
            except Exception:
                pass
        raise


embed_document_docx = embed_docx


def main():

    parser = argparse.ArgumentParser(
        description="DOCX文档水印发行"
    )


    parser.add_argument(
        "input",
    )


    parser.add_argument(
        "--registry",
        default="document_registry.json",
    )


    parser.add_argument(
        "--output",
        required=True,
    )


    parser.add_argument(
        "--key",
        default=DEFAULT_KEY,
    )


    parser.add_argument(
        "--key-id",
        default=None,
    )


    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
    )

    parser.add_argument("--alpha", type=float, default=42.0)
    parser.add_argument("--repeat", type=int, default=16)
    parser.add_argument("--pilot-bits", type=int, default=64)
    parser.add_argument("--pilot-repeat", type=int, default=6)
    parser.add_argument("--pilot-alpha", type=float, default=78.0)


    args = parser.parse_args()


    result = embed_docx(
        args.input,

        args.registry,

        args.output,

        args.key,

        args.key_id,

        args.dpi,

        alpha=args.alpha,

        repeat=args.repeat,

        pilot_bits=args.pilot_bits,

        pilot_repeat=args.pilot_repeat,

        pilot_alpha=args.pilot_alpha,
    )


    print(
        "DOCX水印完成"
    )

    print(
        "Document ID:",
        result["document_id"],
    )

    print(
        "TraceToken:",
        result["trace_token"],
    )

    print(
        "Output:",
        args.output,
    )

    print(
        "Manifest:",
        result["manifest_path"],
    )



if __name__ == "__main__":
    main()
