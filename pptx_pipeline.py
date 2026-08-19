"""PowerPoint watermark issuing pipeline for V2.1.1."""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from document_carrier import (
    issue_watermarked_pages,
)
from document_registry import (
    DOCUMENT_CODEWORD_BITS,
    DOCUMENT_WATERMARK_VERSION,
    attach_issue_artifact,
    sha256_file,
)
from office_renderer import (
    render_pptx_pages,
)
from pptx_rebuilder import (
    rebuild_pptx_from_images,
)
from pdf_pipeline import (
    DEFAULT_KEY,
)


PPTX_PIPELINE_VERSION = "pptx-v2.1.1"


def _write_json(path, data):

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file_obj:

        json.dump(
            data,
            file_obj,
            ensure_ascii=False,
            indent=2,
        )


def _key_id(key):

    import hashlib

    return hashlib.sha256(
        str(key).encode("utf-8")
    ).hexdigest()[:16]


def embed_document_pptx(
    input_pptx,
    registry_path,
    key=DEFAULT_KEY,
    output_pptx=None,
    assets_root=None,
    dpi=96,
    alpha=72.0,
    repeat=24,
    pilot_bits=64,
    pilot_repeat=8,
    pilot_alpha=90.0,
    recipient=None,
    session=None,
    notes=None,
    trace_token=None,
    watermark_number=None,
    source_name=None,
):
    """Generate a flattened watermarked PPTX."""

    input_pptx = Path(
        input_pptx
    ).resolve()

    registry_path = Path(
        registry_path
    ).resolve()

    if not input_pptx.is_file():

        raise FileNotFoundError(
            f"PPTX不存在: {input_pptx}"
        )

    if input_pptx.suffix.lower() != ".pptx":

        raise ValueError(
            "embed_document_pptx只接受.pptx文件"
        )

    # --------------------------------------------------------
    # 1. 用源文件SHA256确定稳定Document ID
    # --------------------------------------------------------

    source_sha256 = sha256_file(
        input_pptx
    )

    document_id_hint = (
        source_sha256[:24]
    )

    # --------------------------------------------------------
    # 2. 文档资产目录
    # --------------------------------------------------------

    if assets_root is None:

        assets_root = (
            input_pptx.parent
            / ".document_assets"
        )

    assets_root = Path(
        assets_root
    ).resolve()

    document_assets = (
        assets_root
        / document_id_hint
    )

    reference_dir = (
        document_assets
        / "reference_pages"
    )

    reference_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 3. 原始PPTX -> Slide PNG
    # --------------------------------------------------------

    rendered_pages, render_info = (
        render_pptx_pages(
            input_pptx,
            reference_dir,
            dpi=dpi,
        )
    )

    # --------------------------------------------------------
    # 4. 构造通用Carrier需要的page_records
    #
    # 为兼容现有照片匹配代码，PPT Slide仍存入pages，
    # page_index即Slide序号。
    # --------------------------------------------------------

    page_records = []

    for page_index, reference_path in enumerate(
        rendered_pages,
        start=1,
    ):

        reference_path = Path(
            reference_path
        ).resolve()

        image = cv2.imread(
            str(reference_path),
            cv2.IMREAD_COLOR,
        )

        if image is None:

            raise RuntimeError(
                "无法读取PowerPoint渲染页面: "
                f"{reference_path}"
            )

        height, width = (
            image.shape[:2]
        )

        page_records.append({
            "page_index":
                page_index,

            "unit_type":
                "slide",

            "slide_index":
                page_index,

            "width":
                int(width),

            "height":
                int(height),

            "reference_path":
                str(reference_path),

            "reference_sha256":
                sha256_file(
                    reference_path
                ),
        })

    # --------------------------------------------------------
    # 5. 通用DCT / Pilot / TraceToken Carrier
    # --------------------------------------------------------

    carrier = issue_watermarked_pages(
        source_path=input_pptx,

        registry_path=registry_path,

        page_records=page_records,

        dpi=dpi,

        media_box_points=[
            render_info[
                "slide_width_points"
            ],
            render_info[
                "slide_height_points"
            ],
        ],

        document_assets=
            document_assets,

        key=key,

        key_id=_key_id(key),

        alpha=alpha,

        repeat=repeat,

        pilot_bits=pilot_bits,

        pilot_repeat=pilot_repeat,

        pilot_alpha=pilot_alpha,

        recipient=recipient,

        session=session,

        notes=notes,

        trace_token=trace_token,

        watermark_number=
            watermark_number,

        source_name=source_name,

        source_type="pptx",

        render_unit_type="slide",
    )

    issue = carrier[
        "issue"
    ]

    token = carrier[
        "trace_token"
    ]

    # --------------------------------------------------------
    # 6. 确定最终水印PPTX文件名
    # --------------------------------------------------------

    if output_pptx is None:

        public_number = (
            issue.get(
                "watermark_number"
            )
            or token
        )

        output_pptx = (
            input_pptx.with_name(
                f"{input_pptx.stem}"
                f"_wm_{public_number}.pptx"
            )
        )

    output_pptx = Path(
        output_pptx
    ).resolve()

    # --------------------------------------------------------
    # 7. Watermarked Slide PNG -> PPTX
    # --------------------------------------------------------

    rebuild_pptx_from_images(
        carrier[
            "embedded_pages"
        ],

        output_pptx,

        slide_width_points=
            render_info[
                "slide_width_points"
            ],

        slide_height_points=
            render_info[
                "slide_height_points"
            ],
    )

    # --------------------------------------------------------
    # 8. Manifest
    # --------------------------------------------------------

    manifest_path = (
        output_pptx
        .with_suffix(
            ".manifest.json"
        )
    )

    manifest = {
        "schema_version":
            1,

        "pipeline_version":
            PPTX_PIPELINE_VERSION,

        # --------------------------------------------------------
        # 物理水印协议版本
        # --------------------------------------------------------

        "watermark_version":
            DOCUMENT_WATERMARK_VERSION,

        "carrier_version":
            DOCUMENT_WATERMARK_VERSION,

        # --------------------------------------------------------
        # 文档类型
        # --------------------------------------------------------

        "source_type":
            "pptx",

        "render_unit_type":
            "slide",

        # --------------------------------------------------------
        # 源文件 / 输出文件
        # --------------------------------------------------------

        "source_path":
            str(input_pptx),

        "source_pptx":
            str(input_pptx),

        "source_sha256":
            source_sha256,

        "output_path":
            str(output_pptx),

        "output_pptx":
            str(output_pptx),

        "output_sha256":
            sha256_file(
                output_pptx
            ),

        # --------------------------------------------------------
        # Document / Issue
        # --------------------------------------------------------

        "document_id":
            carrier[
                "document_id"
            ],

        "page_count":
            len(
                page_records
            ),

        "trace_id":
            carrier[
                "trace_id"
            ],

        "trace_token":
            carrier[
                "trace_token"
            ],

        "watermark_number":
            carrier[
                "watermark_number"
            ],

        "encoded_bits":
            carrier[
                "encoded_bits"
            ],

        # --------------------------------------------------------
        # 渲染几何
        # --------------------------------------------------------

        "dpi":
            int(dpi),

        "media_box_points": [
            float(
                render_info[
                    "slide_width_points"
                ]
            ),

            float(
                render_info[
                    "slide_height_points"
                ]
            ),
        ],

        "rendering":
            render_info,

        # --------------------------------------------------------
        # 非常重要：
        #
        # trace_document_photo()会直接检查这个字段。
        # --------------------------------------------------------

        "key_id":
            _key_id(key),

        # --------------------------------------------------------
        # 非常重要：
        #
        # 这里必须保持和PDF Manifest相同的Carrier配置结构。
        # trace_document_photo()、PN同步、DCT提取都会读取它。
        # --------------------------------------------------------

        "watermark": {
            "block_size":
                8,

            "bit_count":
                DOCUMENT_CODEWORD_BITS,

            "alpha":
                float(alpha),

            "repeat":
                int(repeat),

            "adaptive_alpha":
                True,

            "sync_pilot": {
                "enabled":
                    True,

                "version":
                    "pn64-v1",

                "bit_count":
                    int(
                        pilot_bits
                    ),

                "repeat":
                    int(
                        pilot_repeat
                    ),

                "alpha":
                    float(
                        pilot_alpha
                    ),

                "position_offset":
                    (
                        DOCUMENT_CODEWORD_BITS
                        * int(repeat)
                    ),
            },
        },

    # --------------------------------------------------------
    # PPTX特有信息
    # --------------------------------------------------------

    "output_mode":
        "flattened_slide_images",

    "editable":
        False,

    # --------------------------------------------------------
    # 页面 / Slide
    # --------------------------------------------------------

    "pages":
        carrier[
            "manifest_pages"
        ],
}

    _write_json(
        manifest_path,
        manifest,
    )

    # --------------------------------------------------------
    # 9. 挂到注册库Issue
    #
    # 你前面已经把attach_issue_artifact泛化成
    # output_path / output_type。
    # --------------------------------------------------------

    attach_issue_artifact(
        registry_path,
        token,
        output_pptx,
        manifest_path,
    )

    return (
        manifest_path,
        manifest,
    )