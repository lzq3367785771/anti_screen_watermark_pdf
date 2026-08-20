"""PowerPoint watermark issuing pipeline for V2.1.1."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import cv2

from document_carrier import (
    issue_watermarked_pages,
)

from document_registry import (
    DOCUMENT_CODEWORD_BITS,
    DOCUMENT_WATERMARK_VERSION,
    attach_issue_artifact,
    load_registered_reference_set,
    rollback_unattached_issue,
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
    """Atomically write one PPTX issuance manifest."""

    path = Path(
        path
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = (
        path.with_suffix(
            path.suffix
            + ".tmp"
        )
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

    temporary.replace(
        path
    )


def _key_id(key):

    import hashlib

    return hashlib.sha256(
        str(key).encode("utf-8")
    ).hexdigest()[:16]


def _rollback_pptx_issue_failure(
    registry_path,
    issue_context,
    staging_pptx=None,
    staging_owned=False,
    output_pptx=None,
    output_owned=False,
    output_backup=None,
    manifest_path=None,
    manifest_owned=False,
    manifest_backup=None,
):
    """Best-effort rollback for one uncommitted PPTX issuance.

    Registry rollback is always attempted before filesystem cleanup.

    Files are modified only when the caller has explicit ownership
    information. Pre-existing outputs, manifests and staging files
    must never be deleted merely because their paths exist.

    Cleanup failures are returned as diagnostic strings and must not
    replace the original issuance exception.
    """

    diagnostics = []

    context = (
        issue_context
        if isinstance(issue_context, dict)
        else {}
    )

    # --------------------------------------------------------
    # 1. 本次调用没有成功创建Registry issue：
    #    不允许执行发行级回滚。
    # --------------------------------------------------------

    if not context.get(
        "issue_created"
    ):
        return diagnostics

    token = context.get(
        "trace_token"
    )

    if not token:
        diagnostics.append(
            "issue_created=True，但缺少trace_token"
        )
        return diagnostics

    # --------------------------------------------------------
    # 2. Registry优先。
    #
    # 如果Registry回滚失败，所有文件全部保留。
    # 宁可留下孤儿资产，也不能删掉一个可能已经提交的
    # active issue对应文件。
    # --------------------------------------------------------

    try:
        rollback_unattached_issue(
            registry_path,
            token,
        )

    except Exception as exc:
        diagnostics.append(
            "Registry回滚失败: "
            f"{type(exc).__name__}: {exc}"
        )

        return diagnostics

    # --------------------------------------------------------
    # 3. Carrier生成的token专属水印页面。
    # --------------------------------------------------------

    if context.get(
        "issue_page_dir_created"
    ):
        issue_page_dir = context.get(
            "issue_page_dir"
        )

        if issue_page_dir is not None:
            try:
                issue_page_dir = Path(
                    issue_page_dir
                )

                if issue_page_dir.exists():
                    shutil.rmtree(
                        issue_page_dir
                    )

            except Exception as exc:
                diagnostics.append(
                    "issue页面目录清理失败: "
                    f"{type(exc).__name__}: {exc}"
                )

    # --------------------------------------------------------
    # 4. PPTX最终输出。
    #
    # 如果发行前存在旧output，则output_backup保存旧版本，
    # 失败时恢复。
    #
    # 如果发行前不存在旧output，而本次已经发布了新的final，
    # 则直接删除本次final。
    # --------------------------------------------------------

    if output_pptx is not None:
        output_pptx = Path(
            output_pptx
        )

        try:
            backup = (
                Path(output_backup)
                if output_backup is not None
                else None
            )

            if (
                backup is not None
                and backup.is_file()
            ):
                backup.replace(
                    output_pptx
                )

            elif output_owned:
                if output_pptx.is_file():
                    output_pptx.unlink()

        except Exception as exc:
            diagnostics.append(
                "PPTX输出恢复/清理失败: "
                f"{type(exc).__name__}: {exc}"
            )

    # --------------------------------------------------------
    # 5. Manifest恢复。
    #
    # 与最终PPTX相同：
    #   有旧文件 -> backup恢复
    #   原来没有 -> 删除本次新文件
    #
    # _write_json()现在使用：
    #   <manifest>.tmp
    #
    # manifest_owned=True代表调用前已经确认final/tmp均不存在，
    # 或旧final已经安全移动到backup。
    # --------------------------------------------------------

    if manifest_path is not None:
        manifest_path = Path(
            manifest_path
        )

        try:
            backup = (
                Path(manifest_backup)
                if manifest_backup is not None
                else None
            )

            if (
                backup is not None
                and backup.is_file()
            ):
                backup.replace(
                    manifest_path
                )

            elif manifest_owned:
                if manifest_path.is_file():
                    manifest_path.unlink()

        except Exception as exc:
            diagnostics.append(
                "Manifest恢复/清理失败: "
                f"{type(exc).__name__}: {exc}"
            )

        if manifest_owned:
            try:
                manifest_tmp = (
                    manifest_path.with_suffix(
                        manifest_path.suffix
                        + ".tmp"
                    )
                )

                if manifest_tmp.is_file():
                    manifest_tmp.unlink()

            except Exception as exc:
                diagnostics.append(
                    "Manifest临时文件清理失败: "
                    f"{type(exc).__name__}: {exc}"
                )

    # --------------------------------------------------------
    # 6. token专属staging PPTX。
    #
    # staging从来不是用户正式输出，只要调用前确认不存在，
    # 并设置staging_owned=True，就可以安全删除。
    # --------------------------------------------------------

    if (
        staging_owned
        and staging_pptx is not None
    ):
        try:
            staging_pptx = Path(
                staging_pptx
            )

            if staging_pptx.is_file():
                staging_pptx.unlink()

        except Exception as exc:
            diagnostics.append(
                "PPTX staging清理失败: "
                f"{type(exc).__name__}: {exc}"
            )

    return diagnostics


def _prepare_pptx_reference_set(
    input_pptx,
    registry_path,
    document_assets,
    document_id,
    dpi,
):
    """Prepare or reuse the canonical PPTX reference-slide set.

    Existing registered canonical references are reused read-only.

    For a new document, PowerPoint renders into an owned staging
    directory. The staging directory is published as reference_pages
    only after every returned slide image has been validated.
    """

    input_pptx = Path(
        input_pptx
    ).resolve()

    registry_path = Path(
        registry_path
    ).resolve()

    document_assets = Path(
        document_assets
    ).resolve()

    reference_dir = (
        document_assets
        / "reference_pages"
    )

    # --------------------------------------------------------
    # 1. 已有可信Canonical Reference：
    #
    #    不启动PowerPoint，直接只读复用。
    # --------------------------------------------------------

    registered = (
        load_registered_reference_set(
            registry_path,
            document_id,
            expected_source_type="pptx",
            expected_dpi=dpi,
        )
    )

    if registered is not None:

        media_box = registered.get(
            "media_box_points"
        )

        if (
            not isinstance(
                media_box,
                (list, tuple),
            )
            or len(media_box) != 2
        ):
            raise ValueError(
                "已登记PPTX缺少有效media_box_points"
            )

        pages = [
            dict(page)
            for page
            in registered[
                "pages"
            ]
        ]

        first_page = pages[0]

        # ----------------------------------------------------
        # Registry目前没有独立保存完整render_info。
        #
        # 对Canonical复用路径，只重建当前Pipeline实际需要的
        # 几何信息。
        # ----------------------------------------------------

        render_info = {
            "source_type":
                "pptx",

            "render_unit_type":
                "slide",

            "renderer":
                "canonical_reference_reuse",

            "dpi":
                int(dpi),

            "slide_count":
                len(pages),

            "slide_width_points":
                float(
                    media_box[0]
                ),

            "slide_height_points":
                float(
                    media_box[1]
                ),

            "width":
                int(
                    first_page[
                        "width"
                    ]
                ),

            "height":
                int(
                    first_page[
                        "height"
                    ]
                ),
        }

        return {
            "reused":
                True,

            "reference_dir":
                reference_dir.resolve(),

            "page_records":
                pages,

            "media_box_points": [
                float(
                    media_box[0]
                ),
                float(
                    media_box[1]
                ),
            ],

            "render_info":
                render_info,
        }

    # --------------------------------------------------------
    # 2. Registry没有Document，但是正式reference_pages存在。
    #
    #    不能判断它是不是历史半成品，因此拒绝覆盖。
    # --------------------------------------------------------

    if reference_dir.exists():
        raise FileExistsError(
            "reference_pages已存在，"
            "但Registry中没有可验证的PPTX "
            "Canonical Reference；"
            "拒绝覆盖未知历史目录: "
            f"{reference_dir}"
        )

    document_assets.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 3. 建立本次独占staging目录。
    # --------------------------------------------------------

    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=
                ".reference_pages_staging_",
            dir=
                str(
                    document_assets
                ),
        )
    )

    published = False

    try:

        # ----------------------------------------------------
        # 4. PowerPoint只允许向staging导出。
        #
        # 即使第3张Slide导出失败，
        # 前两张半成品也只会落在staging。
        # ----------------------------------------------------

        (
            rendered_pages,
            render_info,
        ) = render_pptx_pages(
            input_pptx,
            staging_dir,
            dpi=dpi,
        )

        if not rendered_pages:
            raise ValueError(
                "PPTX渲染结果为空"
            )

        if not isinstance(
            render_info,
            dict,
        ):
            raise ValueError(
                "PPTX渲染器返回了非法render_info"
            )

        expected_slide_count = (
            render_info.get(
                "slide_count"
            )
        )

        if expected_slide_count is None:
            raise ValueError(
                "PPTX render_info缺少slide_count"
            )

        if int(
            expected_slide_count
        ) != len(
            rendered_pages
        ):
            raise ValueError(
                "PPTX slide_count与实际渲染页面数量不一致"
            )

        width_points = (
            render_info.get(
                "slide_width_points"
            )
        )

        height_points = (
            render_info.get(
                "slide_height_points"
            )
        )

        if (
            width_points is None
            or height_points is None
            or float(width_points) <= 0
            or float(height_points) <= 0
        ):
            raise ValueError(
                "PPTX渲染器返回了非法Slide尺寸"
            )

        staged_records = []

        # ----------------------------------------------------
        # 5. 验证每一张Slide。
        # ----------------------------------------------------

        for slide_index, rendered_page in enumerate(
            rendered_pages,
            start=1,
        ):

            rendered_page = Path(
                rendered_page
            ).resolve()

            # ------------------------------------------------
            # render_pptx_pages()理论上应该只返回staging内部文件。
            #
            # 这里再锁一次边界，避免未来renderer修改后把外部文件
            # 错当成Canonical资产。
            # ------------------------------------------------

            if (
                rendered_page.parent
                != staging_dir.resolve()
            ):
                raise ValueError(
                    "PPTX渲染页面不在本次staging目录内: "
                    f"{rendered_page}"
                )

            if not rendered_page.is_file():
                raise FileNotFoundError(
                    "PPTX渲染页面不存在: "
                    f"{rendered_page}"
                )

            image = cv2.imread(
                str(
                    rendered_page
                ),
                cv2.IMREAD_COLOR,
            )

            if image is None:
                raise RuntimeError(
                    "无法读取PowerPoint渲染页面: "
                    f"{rendered_page}"
                )

            height, width = (
                image.shape[:2]
            )

            staged_records.append({
                "page_index":
                    slide_index,

                "unit_type":
                    "slide",

                "slide_index":
                    slide_index,

                "width":
                    int(width),

                "height":
                    int(height),

                "_filename":
                    rendered_page.name,

                "reference_sha256":
                    sha256_file(
                        rendered_page
                    ),
            })

        # ----------------------------------------------------
        # 6. 构造发布以后真正登记到Registry的final路径。
        #
        # Registry绝不能记录staging路径。
        # ----------------------------------------------------

        page_records = []

        for staged_record in staged_records:

            final_reference = (
                reference_dir
                / staged_record[
                    "_filename"
                ]
            ).resolve()

            page_records.append({
                "page_index":
                    staged_record[
                        "page_index"
                    ],

                "unit_type":
                    "slide",

                "slide_index":
                    staged_record[
                        "slide_index"
                    ],

                "width":
                    staged_record[
                        "width"
                    ],

                "height":
                    staged_record[
                        "height"
                    ],

                "reference_path":
                    str(
                        final_reference
                    ),

                "reference_sha256":
                    staged_record[
                        "reference_sha256"
                    ],
            })

        media_box = [
            float(
                width_points
            ),
            float(
                height_points
            ),
        ]

        # ----------------------------------------------------
        # 7. 发布前再次确认final没有被别人创建。
        # ----------------------------------------------------

        if reference_dir.exists():
            raise FileExistsError(
                "准备发布PPTX Canonical Reference时"
                "reference_pages已经存在: "
                f"{reference_dir}"
            )

        # ----------------------------------------------------
        # 8. 整目录一次性发布。
        # ----------------------------------------------------

        staging_dir.replace(
            reference_dir
        )

        published = True

        return {
            "reused":
                False,

            "reference_dir":
                reference_dir.resolve(),

            "page_records":
                page_records,

            "media_box_points":
                media_box,

            "render_info":
                dict(
                    render_info
                ),
        }

    finally:

        # ----------------------------------------------------
        # 失败只清理本次staging。
        #
        # PowerPoint可能已经导出了slide_001.png等半成品，
        # 它们全部随着staging一起删除。
        # ----------------------------------------------------

        if (
            not published
            and staging_dir.exists()
        ):
            shutil.rmtree(
                staging_dir,
                ignore_errors=True,
            )


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

    # --------------------------------------------------------
    # 3. Canonical Reference
    #
    # 已登记且验证完整：
    #   直接只读复用，不启动PowerPoint。
    #
    # 首次建立：
    #   PowerPoint只向staging导出，
    #   完整验证后才发布reference_pages。
    # --------------------------------------------------------

    reference = (
        _prepare_pptx_reference_set(
            input_pptx=
                input_pptx,

            registry_path=
                registry_path,

            document_assets=
                document_assets,

            document_id=
                document_id_hint,

            dpi=
                dpi,
        )
    )

    page_records = [
        dict(page)
        for page
        in reference[
            "page_records"
        ]
    ]

    render_info = dict(
        reference[
            "render_info"
        ]
    )

    media_box_points = [
        float(value)
        for value
        in reference[
            "media_box_points"
        ]
    ]

    # --------------------------------------------------------
    # 5. PPTX发行事务状态
    # --------------------------------------------------------

    issue_context = {}

    staging_pptx = None
    staging_owned = False

    output_owned = False
    output_backup = None

    manifest_path = None
    manifest_owned = False
    manifest_backup = None

    committed = False

    try:

        carrier = issue_watermarked_pages(
            source_path=input_pptx,

            registry_path=registry_path,

            page_records=page_records,

            dpi=dpi,

            media_box_points=
                media_box_points,

            document_assets=
                document_assets,

            key=key,

            key_id=_key_id(key),

            alpha=alpha,

            repeat=repeat,

            pilot_bits=pilot_bits,

            pilot_repeat=pilot_repeat,

            pilot_alpha=pilot_alpha,

            payload_flat_bright_scale=0.75,
            
            pilot_flat_bright_scale=0.60,

            recipient=recipient,

            session=session,

            notes=notes,

            trace_token=trace_token,

            watermark_number=
                watermark_number,

            source_name=source_name,

            source_type="pptx",

            render_unit_type="slide",

            issue_context=issue_context,
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


        if (
            output_pptx.suffix.lower()
            != ".pptx"
        ):
            raise ValueError(
                "PPTX输出路径必须以.pptx结尾"
            )

        if output_pptx == input_pptx:
            raise ValueError(
                "PPTX输出路径不能覆盖源PPTX"
            )


        # --------------------------------------------------------
        # 7. token专属PPTX staging
        #
        # PowerPoint绝不能直接SaveAs最终用户输出。
        # --------------------------------------------------------

        staging_pptx = (
            output_pptx.with_name(
                output_pptx.name
                + "."
                + token
                + ".staging.pptx"
            )
        )

        if staging_pptx.exists():
            raise FileExistsError(
                "PPTX staging文件已存在，"
                "拒绝覆盖未知历史文件: "
                f"{staging_pptx}"
            )

        # 从这一刻起，如果staging路径上出现文件，
        # 就一定属于本次发行。
        staging_owned = True


        # --------------------------------------------------------
        # 7. Watermarked Slide PNG -> PPTX
        # --------------------------------------------------------

        rebuild_pptx_from_images(
            carrier[
                "embedded_pages"
            ],

            staging_pptx,

            slide_width_points=
                render_info[
                    "slide_width_points"
                ],

            slide_height_points=
                render_info[
                    "slide_height_points"
                ],
        )


        if not staging_pptx.is_file():
            raise RuntimeError(
                "PPTX重建结束但staging文件不存在: "
                f"{staging_pptx}"
            )

        # --------------------------------------------------------
        # 如果用户原来的final已经存在，先保存旧版本。
        # --------------------------------------------------------

        if output_pptx.exists():

            if not output_pptx.is_file():
                raise FileExistsError(
                    "PPTX输出路径已存在且不是文件: "
                    f"{output_pptx}"
                )

            output_backup = (
                output_pptx.with_name(
                    output_pptx.name
                    + "."
                    + token
                    + ".rollback.bak"
                )
            )

            if output_backup.exists():
                raise FileExistsError(
                    "PPTX回滚备份已存在，"
                    "拒绝覆盖未知历史文件: "
                    f"{output_backup}"
                )

            output_pptx.replace(
                output_backup
            )

        # --------------------------------------------------------
        # staging -> final
        #
        # 到这里以后，final路径上的新PPTX才属于本次发行。
        # --------------------------------------------------------

        staging_pptx.replace(
            output_pptx
        )

        staging_owned = False
        output_owned = True

        # --------------------------------------------------------
        # 8. Manifest
        # --------------------------------------------------------

        manifest_path = (
            output_pptx
            .with_suffix(
                ".manifest.json"
            )
        )


        manifest_tmp = (
            manifest_path.with_suffix(
                manifest_path.suffix
                + ".tmp"
            )
        )

        manifest_backup = (
            manifest_path.with_name(
                manifest_path.name
                + "."
                + token
                + ".rollback.bak"
            )
        )

        # --------------------------------------------------------
        # .tmp 如果发行开始前已经存在，就不是我们的。
        # --------------------------------------------------------

        if manifest_tmp.exists():
            raise FileExistsError(
                "PPTX Manifest临时文件已存在，"
                "拒绝覆盖未知历史文件: "
                f"{manifest_tmp}"
            )

        if manifest_backup.exists():
            raise FileExistsError(
                "PPTX Manifest回滚备份已存在，"
                "拒绝覆盖未知历史文件: "
                f"{manifest_backup}"
            )

        # --------------------------------------------------------
        # 旧Manifest存在时先保存。
        # --------------------------------------------------------

        if manifest_path.exists():

            if not manifest_path.is_file():
                raise FileExistsError(
                    "PPTX Manifest路径已存在且不是文件: "
                    f"{manifest_path}"
                )

            manifest_path.replace(
                manifest_backup
            )

        # 从这里开始，如果manifest final/tmp出现，
        # 都属于本次发行。
        manifest_owned = True

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
        # --------------------------------------------------------
        # 逻辑COMMIT：
        # Registry已经正式关联PPTX + Manifest。
        # --------------------------------------------------------

        committed = True

        # --------------------------------------------------------
        # 成功提交后旧文件备份不再需要。
        #
        # 删除失败不能把已经成功提交的发行变成失败。
        # --------------------------------------------------------

        for backup in (
            output_backup,
            manifest_backup,
        ):
            if (
                backup is not None
                and Path(backup).is_file()
            ):
                try:
                    Path(backup).unlink()

                except OSError:
                    pass

        return (
            manifest_path,
            manifest,
        )

    except Exception as exc:

        # --------------------------------------------------------
        # attach成功以后已经COMMIT，绝不能再回滚。
        # --------------------------------------------------------

        if committed:
            raise

        rollback_diagnostics = []

        try:
            rollback_diagnostics = (
                _rollback_pptx_issue_failure(
                    registry_path=
                        registry_path,

                    issue_context=
                        issue_context,

                    staging_pptx=
                        staging_pptx,

                    staging_owned=
                        staging_owned,

                    output_pptx=
                        output_pptx,

                    output_owned=
                        output_owned,

                    output_backup=
                        output_backup,

                    manifest_path=
                        manifest_path,

                    manifest_owned=
                        manifest_owned,

                    manifest_backup=
                        manifest_backup,
                )
            )

        except Exception as rollback_exc:

            rollback_diagnostics = [
                (
                    "PPTX回滚执行器异常: "
                    f"{type(rollback_exc).__name__}: "
                    f"{rollback_exc}"
                )
            ]

        # --------------------------------------------------------
        # 回滚错误只做附加诊断，
        # 绝不覆盖真正导致发行失败的原异常。
        # --------------------------------------------------------

        if rollback_diagnostics:

            try:
                setattr(
                    exc,
                    "rollback_diagnostics",
                    rollback_diagnostics,
                )

            except Exception:
                pass

        raise