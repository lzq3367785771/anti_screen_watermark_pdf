"""Office document rendering adapters for V2.1.x.

Current implementation:

    PPTX -> rendered slide PNG images

The watermark algorithm is deliberately NOT implemented here.

This module is responsible only for converting Office document visual units
into raster images that can later be passed to ``document_carrier.py``.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

class OfficeRendererUnavailable(RuntimeError):
    """Raised when the required Microsoft Office rendering backend is missing."""


def _points_to_pixels(points, dpi):
    """Convert PowerPoint point units to raster pixels.

    PowerPoint slide dimensions are expressed in points:

        72 points = 1 inch
    """

    points = float(points)
    dpi = float(dpi)

    if points <= 0:
        raise ValueError(
            "PowerPoint页面尺寸必须大于0"
        )

    if dpi <= 0:
        raise ValueError(
            "DPI必须大于0"
        )

    return max(
        1,
        int(round(points * dpi / 72.0)),
    )


def _slide_pixel_size(
    width_points,
    height_points,
    dpi,
):
    """Return the raster width/height for one PowerPoint slide."""

    return (
        _points_to_pixels(
            width_points,
            dpi,
        ),
        _points_to_pixels(
            height_points,
            dpi,
        ),
    )


def _load_powerpoint_backend():
    """Load pywin32 COM support lazily.

    Keeping the import local allows the rest of the project to operate even
    when PowerPoint support is not installed.
    """

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise OfficeRendererUnavailable(
            "未安装PowerPoint渲染依赖pywin32。"
            "请执行: python -m pip install pywin32"
        ) from exc

    return pythoncom, win32com.client


def _load_word_backend():
    """Load pywin32 COM support for Microsoft Word lazily."""

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise OfficeRendererUnavailable(
            "未安装Word渲染依赖pywin32。"
            "请执行: python -m pip install pywin32"
        ) from exc

    return pythoncom, win32com.client



def check_powerpoint_backend():
    """Check whether Microsoft PowerPoint COM automation is available."""

    pythoncom, win32_client = (
        _load_powerpoint_backend()
    )

    pythoncom.CoInitialize()

    application = None

    try:
        application = (
            win32_client.DispatchEx(
                "PowerPoint.Application"
            )
        )

        version = str(
            application.Version
        )

        return {
            "available": True,
            "version": version,
        }

    except Exception as exc:
        raise OfficeRendererUnavailable(
            "无法启动Microsoft PowerPoint。"
            "请确认Windows中已经安装桌面版PowerPoint。"
        ) from exc

    finally:
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass

        pythoncom.CoUninitialize()


def check_word_backend():
    """Check whether Microsoft Word COM automation is available."""

    pythoncom, win32_client = (
        _load_word_backend()
    )

    pythoncom.CoInitialize()

    application = None

    try:
        try:
            application = (
                win32_client.DispatchEx(
                    "Word.Application"
                )
            )
        except Exception as exc:
            raise OfficeRendererUnavailable(
                "无法启动Microsoft Word。"
                "请确认Windows中已经安装桌面版Word。"
            ) from exc

        version = str(
            application.Version
        )

        return {
            "available": True,
            "version": version,
        }

    finally:
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass

        pythoncom.CoUninitialize()




def render_pptx_pages(
    pptx_path,
    output_dir,
    dpi=96,
):
    """Render every PPTX slide into a deterministic PNG image.

    Parameters
    ----------
    pptx_path:
        Source .pptx file.

    output_dir:
        Directory used to store:

            slide_001.png
            slide_002.png
            ...

    dpi:
        Target raster resolution.

    Returns
    -------
    tuple[list[Path], dict]

        rendered slide paths
        +
        rendering metadata
    """

    pptx_path = Path(
        pptx_path
    ).resolve()

    output_dir = Path(
        output_dir
    ).resolve()

    if not pptx_path.is_file():
        raise FileNotFoundError(
            f"PPTX不存在: {pptx_path}"
        )

    if pptx_path.suffix.lower() != ".pptx":
        raise ValueError(
            "render_pptx_pages只接受.pptx文件"
        )

    dpi = int(dpi)

    if dpi <= 0:
        raise ValueError(
            "DPI必须大于0"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    pythoncom, win32_client = (
        _load_powerpoint_backend()
    )

    # --------------------------------------------------------
    # ThreadingHTTPServer以后也可能调用这个函数。
    #
    # COM必须在当前线程初始化，所以不能只在程序启动时初始化一次。
    # --------------------------------------------------------

    pythoncom.CoInitialize()

    application = None
    presentation = None

    try:

        # ----------------------------------------------------
        # 1. 启动独立PowerPoint COM实例
        # ----------------------------------------------------

        try:
            application = (
                win32_client.DispatchEx(
                    "PowerPoint.Application"
                )
            )
        except Exception as exc:
            raise OfficeRendererUnavailable(
                "无法启动Microsoft PowerPoint。"
                "请确认已经安装桌面版PowerPoint。"
            ) from exc

        # ----------------------------------------------------
        # 2. 只读、无窗口打开PPTX
        #
        # Open参数：
        # FileName
        # ReadOnly
        # Untitled
        # WithWindow
        # ----------------------------------------------------

        presentation = (
            application.Presentations.Open(
                str(pptx_path),
                True,
                False,
                False,
            )
        )

        slide_count = int(
            presentation.Slides.Count
        )

        if slide_count <= 0:
            raise RuntimeError(
                "PPTX中没有幻灯片"
            )

        # ----------------------------------------------------
        # 3. 获取原始Slide尺寸
        #
        # PowerPoint使用point：
        # 72 point = 1 inch
        # ----------------------------------------------------

        width_points = float(
            presentation.PageSetup.SlideWidth
        )

        height_points = float(
            presentation.PageSetup.SlideHeight
        )

        width_px, height_px = (
            _slide_pixel_size(
                width_points,
                height_points,
                dpi,
            )
        )

        rendered_pages = []

        # ----------------------------------------------------
        # 4. 逐Slide导出
        #
        # 不依赖PowerPoint自动生成的Slide1.png名字。
        # 我们自己定义固定名称。
        # ----------------------------------------------------

        for slide_index in range(
            1,
            slide_count + 1,
        ):

            slide = (
                presentation.Slides(
                    slide_index
                )
            )

            target = (
                output_dir
                / (
                    f"slide_"
                    f"{slide_index:03d}.png"
                )
            )

            slide.Export(
                str(target),
                "PNG",
                width_px,
                height_px,
            )

            if not target.is_file():
                raise RuntimeError(
                    "PowerPoint未生成预期Slide图像: "
                    f"{target}"
                )

            rendered_pages.append(
                target
            )

        return rendered_pages, {
            "source_type": "pptx",

            "render_unit_type":
                "slide",

            "renderer":
                "microsoft_powerpoint_com",

            "dpi":
                dpi,

            "slide_count":
                slide_count,

            "slide_width_points":
                width_points,

            "slide_height_points":
                height_points,

            "width":
                width_px,

            "height":
                height_px,
        }

    finally:

        # ----------------------------------------------------
        # 5. 一定关闭Presentation和PowerPoint
        #
        # 否则可能留下POWERPNT.EXE后台进程。
        # ----------------------------------------------------

        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass

        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass

        pythoncom.CoUninitialize()


def render_docx_pages(
    docx_path,
    output_dir,
    dpi=150,
    poppler_bin=None,
):
    """Render a DOCX document into deterministic PNG page images.

    Rendering pipeline:

        DOCX
          -> Microsoft Word COM
          -> temporary PDF
          -> existing PDF renderer
          -> page_001.png
          -> page_002.png
          -> ...

    Parameters
    ----------
    docx_path:
        Source .docx file.

    output_dir:
        Directory used to store rendered page PNG images.

    dpi:
        Target raster resolution.

    poppler_bin:
        Optional Poppler binary directory passed through to the
        existing PDF renderer.

    Returns
    -------
    tuple[list[Path], dict]

        rendered page paths
        +
        rendering metadata
    """

    docx_path = Path(
        docx_path
    ).resolve()

    output_dir = Path(
        output_dir
    ).resolve()

    # --------------------------------------------------------
    # 1. Basic validation
    # --------------------------------------------------------

    if not docx_path.is_file():
        raise FileNotFoundError(
            f"DOCX不存在: {docx_path}"
        )

    if docx_path.suffix.lower() != ".docx":
        raise ValueError(
            "render_docx_pages只接受.docx文件"
        )

    dpi = int(dpi)

    if dpi <= 0:
        raise ValueError(
            "DPI必须大于0"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Local import is intentional.
    #
    # office_renderer is an adapter module. Importing the
    # existing PDF renderer lazily avoids adding unnecessary
    # module-level coupling to the PDF pipeline.
    # --------------------------------------------------------

    from pdf_pipeline import (
        render_pdf_pages,
    )

    # --------------------------------------------------------
    # 2. Temporary PDF belongs only to this renderer call.
    #
    # It must never become part of the Canonical Reference
    # directory.
    # --------------------------------------------------------

    with TemporaryDirectory(
        prefix="docx_render_"
    ) as temporary_dir_value:

        temporary_dir = Path(
            temporary_dir_value
        )

        temporary_pdf = (
            temporary_dir
            / "word_export.pdf"
        )

        pythoncom, win32_client = (
            _load_word_backend()
        )

        pythoncom.CoInitialize()

        application = None
        document = None

        try:

            # ------------------------------------------------
            # 3. Start an isolated Microsoft Word instance.
            # ------------------------------------------------

            try:
                application = (
                    win32_client.DispatchEx(
                        "Word.Application"
                    )
                )
            except Exception as exc:
                raise OfficeRendererUnavailable(
                    "无法启动Microsoft Word。"
                    "请确认已经安装桌面版Word。"
                ) from exc

            # Do not show Word UI or modal alert dialogs.
            try:
                application.Visible = False
            except Exception:
                pass

            try:
                application.DisplayAlerts = 0
            except Exception:
                pass

            # ------------------------------------------------
            # 4. Open the source DOCX read-only.
            # ------------------------------------------------

            try:
                document = (
                    application.Documents.Open(
                        str(docx_path),
                        ConfirmConversions=False,
                        ReadOnly=True,
                        AddToRecentFiles=False,
                        Visible=False,
                    )
                )
            except Exception as exc:
                raise RuntimeError(
                    "Microsoft Word无法打开DOCX: "
                    f"{docx_path}"
                ) from exc

            # ------------------------------------------------
            # 5. Ask Word to calculate the final layout before
            #    exporting.
            #
            # This keeps pagination responsibility inside Word
            # instead of trying to reproduce it ourselves.
            # ------------------------------------------------

            try:
                document.Repaginate()
            except Exception:
                pass

            # ------------------------------------------------
            # 6. Export the whole document as PDF.
            #
            # Word constant:
            #   wdExportFormatPDF = 17
            # ------------------------------------------------

            try:
                document.ExportAsFixedFormat(
                    str(temporary_pdf),
                    17,
                )
            except Exception as exc:
                raise RuntimeError(
                    "Microsoft Word导出PDF失败: "
                    f"{docx_path}"
                ) from exc

        finally:

            # ------------------------------------------------
            # 7. Release Word BEFORE invoking pdftoppm.
            #
            # This avoids keeping the exported PDF locked by
            # WINWORD.EXE during the next rendering stage.
            # ------------------------------------------------

            if document is not None:
                try:
                    document.Close(
                        False
                    )
                except Exception:
                    pass

            if application is not None:
                try:
                    application.Quit()
                except Exception:
                    pass

            pythoncom.CoUninitialize()

        # ----------------------------------------------------
        # 8. Word claimed to export successfully, but we still
        #    verify the actual artifact.
        # ----------------------------------------------------

        if not temporary_pdf.is_file():
            raise RuntimeError(
                "Microsoft Word未生成预期PDF: "
                f"{temporary_pdf}"
            )

        if temporary_pdf.stat().st_size <= 0:
            raise RuntimeError(
                "Microsoft Word生成了空PDF: "
                f"{temporary_pdf}"
            )

        # ----------------------------------------------------
        # 9. Reuse the existing PDF renderer.
        #
        # Output names will therefore stay consistent:
        #
        #     page_001.png
        #     page_002.png
        #     ...
        # ----------------------------------------------------

        rendered_pages, renderer_stderr = (
            render_pdf_pages(
                temporary_pdf,
                output_dir,
                dpi=dpi,
                poppler_bin=poppler_bin,
            )
        )

        if not rendered_pages:
            raise RuntimeError(
                "DOCX渲染没有产生页面"
            )

        # ----------------------------------------------------
        # 10. Record per-page raster geometry.
        #
        # Word documents may contain different sections,
        # orientations or page sizes, so we intentionally do
        # not assume that every page has one global width and
        # height.
        # ----------------------------------------------------

        page_sizes = []

        try:
            from PIL import Image
        except ImportError as exc:
            raise OfficeRendererUnavailable(
                "DOCX页面尺寸检查需要Pillow"
            ) from exc

        for index, page_path in enumerate(
            rendered_pages,
            start=1,
        ):
            page_path = Path(
                page_path
            )

            if not page_path.is_file():
                raise RuntimeError(
                    "DOCX渲染页面不存在: "
                    f"{page_path}"
                )

            with Image.open(
                page_path
            ) as image:
                width, height = (
                    image.size
                )

            if (
                int(width) <= 0
                or int(height) <= 0
            ):
                raise RuntimeError(
                    "DOCX渲染页面尺寸非法: "
                    f"page={index}"
                )

            page_sizes.append({
                "page_index":
                    int(index),

                "width":
                    int(width),

                "height":
                    int(height),
            })

        return rendered_pages, {
            "source_type":
                "docx",

            "render_unit_type":
                "page",

            "renderer":
                "microsoft_word_com_pdf",

            "dpi":
                int(dpi),

            "page_count":
                len(rendered_pages),

            "page_sizes":
                page_sizes,

            "pdf_renderer_stderr":
                str(
                    renderer_stderr
                    or ""
                ),
        }