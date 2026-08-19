"""Rebuild a flattened PowerPoint presentation from watermarked slide images.

V2.1.1 output model:

    one rendered watermarked PNG
        =
    one full-slide picture

The resulting PPTX preserves the visual appearance and slide geometry,
but intentionally does not preserve editable PowerPoint objects.
"""

from __future__ import annotations

from pathlib import Path

from office_renderer import (
    OfficeRendererUnavailable,
    _load_powerpoint_backend,
)


# PowerPoint constants.
#
# Microsoft PpSlideLayout:
#     ppLayoutBlank = 12
#
# Microsoft PpSaveAsFileType:
#     ppSaveAsOpenXMLPresentation = 24
PP_LAYOUT_BLANK = 12
PP_SAVE_AS_OPENXML_PRESENTATION = 24

# Microsoft Office MsoTriState:
M_SO_FALSE = 0
M_SO_TRUE = -1


def rebuild_pptx_from_images(
    image_paths,
    output_pptx,
    slide_width_points,
    slide_height_points,
):
    """Create a flattened PPTX from full-slide PNG images.

    Parameters
    ----------
    image_paths:
        按幻灯片顺序排列的PNG文件。

    output_pptx:
        最终生成的.pptx路径。

    slide_width_points:
        原始PPT宽度，单位point。

    slide_height_points:
        原始PPT高度，单位point。

    Returns
    -------
    Path
        最终PPTX的绝对路径。
    """

    images = [
        Path(path).resolve()
        for path in image_paths
    ]

    if not images:
        raise ValueError(
            "没有可用于重建PPTX的水印Slide图像"
        )

    for image_path in images:

        if not image_path.is_file():
            raise FileNotFoundError(
                f"水印Slide图像不存在: {image_path}"
            )

    output_pptx = Path(
        output_pptx
    ).resolve()

    if output_pptx.suffix.lower() != ".pptx":
        raise ValueError(
            "PPTX输出路径必须以.pptx结尾"
        )

    width_points = float(
        slide_width_points
    )

    height_points = float(
        slide_height_points
    )

    if (
        width_points <= 0
        or height_points <= 0
    ):
        raise ValueError(
            "PPTX页面尺寸必须大于0"
        )

    output_pptx.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    pythoncom, win32_client = (
        _load_powerpoint_backend()
    )

    pythoncom.CoInitialize()

    application = None
    presentation = None

    try:

        # ----------------------------------------------------
        # 1. 启动独立PowerPoint实例
        # ----------------------------------------------------

        try:
            application = (
                win32_client.DispatchEx(
                    "PowerPoint.Application"
                )
            )

        except Exception as exc:

            raise OfficeRendererUnavailable(
                "无法启动Microsoft PowerPoint，"
                "不能重建水印PPTX。"
            ) from exc

        # ----------------------------------------------------
        # 2. 创建新的空Presentation
        #
        # False表示不打开可见窗口。
        # ----------------------------------------------------

        presentation = (
            application.Presentations.Add(
                False
            )
        )

        # ----------------------------------------------------
        # 3. 保持与原始PPT完全相同的Slide尺寸
        #
        # PowerPoint PageSetup使用point。
        # ----------------------------------------------------

        presentation.PageSetup.SlideWidth = (
            width_points
        )

        presentation.PageSetup.SlideHeight = (
            height_points
        )

        # 某些PowerPoint版本创建Presentation时可能已经带有Slide。
        # 为了确保最终数量完全由我们的PNG决定，全部删除。
        while (
            presentation.Slides.Count > 0
        ):
            presentation.Slides(1).Delete()

        # ----------------------------------------------------
        # 4. 每一张PNG建立一个空白Slide
        # ----------------------------------------------------

        for slide_index, image_path in enumerate(
            images,
            start=1,
        ):

            slide = (
                presentation.Slides.Add(
                    slide_index,
                    PP_LAYOUT_BLANK,
                )
            )

            # ------------------------------------------------
            # 5. 将水印PNG铺满整个Slide
            #
            # LinkToFile=False
            # SaveWithDocument=True
            #
            # 表示图片真正嵌入PPTX，
            # 而不是依赖外部PNG路径。
            # ------------------------------------------------

            slide.Shapes.AddPicture(
                str(image_path),

                M_SO_FALSE,
                M_SO_TRUE,

                0.0,
                0.0,

                width_points,
                height_points,
            )

        # ----------------------------------------------------
        # 6. 保存为标准Open XML PPTX
        # ----------------------------------------------------

        presentation.SaveAs(
            str(output_pptx),
            PP_SAVE_AS_OPENXML_PRESENTATION,
        )

        if not output_pptx.is_file():

            raise RuntimeError(
                "PowerPoint未生成预期PPTX: "
                f"{output_pptx}"
            )

        return output_pptx

    finally:

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