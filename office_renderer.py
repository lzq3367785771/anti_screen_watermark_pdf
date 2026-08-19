"""Office document rendering adapters for V2.1.x.

Current implementation:

    PPTX -> rendered slide PNG images

The watermark algorithm is deliberately NOT implemented here.

This module is responsible only for converting Office document visual units
into raster images that can later be passed to ``document_carrier.py``.
"""

from __future__ import annotations

from pathlib import Path


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