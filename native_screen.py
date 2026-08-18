# 物理屏显与图像矫正
# 如何把水印图精确地显示在真实屏幕上，以及如何把手机拍摄的照片精准地矫正回原始的数字图像.




import ctypes
import sys

import cv2
import numpy as np

from sync_aruco import detect_markers, generate_marker, get_dictionary


MARKER_IDS = [0, 1, 2, 3]


def enable_physical_pixel_coordinates():
    """让Windows进程读取物理屏幕像素，避免DPI虚拟化。"""

    if sys.platform != "win32":
        return

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def get_primary_screen_size():
    """返回主显示器物理像素尺寸。"""

    enable_physical_pixel_coordinates()

    if sys.platform != "win32":
        raise RuntimeError(
            "非Windows系统请通过 --screen-width 和 --screen-height 指定分辨率"
        )

    width = int(ctypes.windll.user32.GetSystemMetrics(0))
    height = int(ctypes.windll.user32.GetSystemMetrics(1))

    if width <= 0 or height <= 0:
        raise RuntimeError("无法读取主显示器分辨率")

    return width, height


def default_marker_size(screen_width, screen_height):
    """给出既能被手机识别、又尽量少占边缘空间的Marker尺寸。"""

    raw_size = max(48, min(screen_width, screen_height) // 16)
    return max(48, (raw_size // 8) * 8)


def choose_content_size(
        source_width,
        source_height,
        screen_width,
        screen_height,
        marker_size,
        edge_pad,
        align=16
):
    """在Marker边缘区域以内选择保持原比例的最终显示尺寸。"""

    reserved = marker_size + 2 * edge_pad
    max_width = screen_width - 2 * reserved
    max_height = screen_height - 2 * reserved

    if max_width <= 0 or max_height <= 0:
        raise ValueError("屏幕尺寸不足以放置内容和四角Marker")

    scale = min(
        max_width / source_width,
        max_height / source_height
    )

    content_width = int(source_width * scale)
    content_width = max(align, (content_width // align) * align)
    content_height = int(round(content_width * source_height / source_width))

    if content_height > max_height:
        content_height = max_height
        content_height = max(align, (content_height // align) * align)
        content_width = int(round(content_height * source_width / source_height))
        content_width = max(align, (content_width // align) * align)

    return content_width, content_height


def _marker_positions(screen_width, screen_height, marker_size, edge_pad):
    return {
        0: (edge_pad, edge_pad),
        1: (screen_width - edge_pad - marker_size, edge_pad),
        2: (
            screen_width - edge_pad - marker_size,
            screen_height - edge_pad - marker_size
        ),
        3: (edge_pad, screen_height - edge_pad - marker_size)
    }


def _marker_corners(x, y, marker_size):
    return np.float32([
        [x, y],
        [x + marker_size - 1, y],
        [x + marker_size - 1, y + marker_size - 1],
        [x, y + marker_size - 1]
    ])


def create_native_screen_board(
        watermarked_image,
        screen_width,
        screen_height,
        marker_size=None,
        edge_pad=None
):
    """创建尺寸与主显示器物理像素完全一致的1:1实验画布。"""

    if watermarked_image is None:
        raise ValueError("watermarked_image为空")

    if marker_size is None:
        marker_size = default_marker_size(screen_width, screen_height)

    if edge_pad is None:
        edge_pad = max(8, marker_size // 8)

    content_height, content_width = watermarked_image.shape[:2]
    content_x = (screen_width - content_width) // 2
    content_y = (screen_height - content_height) // 2

    if content_x < marker_size + edge_pad or content_y < marker_size + edge_pad:
        raise ValueError(
            "最终内容尺寸过大，四角Marker会与内容重叠；请减小内容尺寸"
        )

    board = np.full(
        (screen_height, screen_width, 3),
        255,
        dtype=np.uint8
    )

    board[
        content_y:content_y + content_height,
        content_x:content_x + content_width
    ] = watermarked_image

    dictionary = get_dictionary()
    positions = _marker_positions(
        screen_width,
        screen_height,
        marker_size,
        edge_pad
    )

    for marker_id in MARKER_IDS:
        marker = generate_marker(dictionary, marker_id, marker_size)
        marker_bgr = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        x, y = positions[marker_id]
        board[y:y + marker_size, x:x + marker_size] = marker_bgr

    layout = {
        "screen_width": int(screen_width),
        "screen_height": int(screen_height),
        "content_rect": {
            "x": int(content_x),
            "y": int(content_y),
            "width": int(content_width),
            "height": int(content_height)
        },
        "marker_size": int(marker_size),
        "edge_pad": int(edge_pad),
        "marker_positions": {
            str(marker_id): {
                "x": int(positions[marker_id][0]),
                "y": int(positions[marker_id][1])
            }
            for marker_id in MARKER_IDS
        }
    }

    return board, layout


def _geometry_grade(marker_ids, layout):
    """根据可见Marker数量与空间分布给出降级等级。"""

    marker_count = len(marker_ids)

    if marker_count == 4:
        return "A", "four_markers"
    if marker_count == 3:
        return "B", "three_markers"
    if marker_count == 1:
        return "E", "single_marker_extrapolation"

    positions = layout["marker_positions"]
    first = positions[str(marker_ids[0])]
    second = positions[str(marker_ids[1])]
    dx = abs(float(first["x"]) - float(second["x"]))
    dy = abs(float(first["y"]) - float(second["y"]))
    screen_width = float(layout["screen_width"])
    screen_height = float(layout["screen_height"])

    if dx > 0.5 * screen_width and dy > 0.5 * screen_height:
        return "C", "two_diagonal_markers"

    return "D", "two_adjacent_markers"


def rectify_native_screen(
        observed_image,
        layout,
        interpolation=cv2.INTER_LINEAR,
        mask_erode_px=3,
        affine_max_error_px=2.5
):
    """使用1～4个Marker降级恢复屏幕，同时返回真实拍摄覆盖Mask。"""

    if observed_image is None:
        raise ValueError("observed_image为空")

    corners, ids, _ = detect_markers(observed_image)

    if ids is None:
        raise RuntimeError("NO_MARKER: 没有检测到任何ArUco Marker")

    detected_ids = ids.flatten().tolist()
    visible_ids = [
        marker_id
        for marker_id in MARKER_IDS
        if marker_id in detected_ids
    ]

    if not visible_ids:
        raise RuntimeError("NO_MARKER: 未检测到实验布局中的Marker")

    marker_size = int(layout["marker_size"])
    src_points = []
    dst_points = []
    src_centers = []
    dst_centers = []

    for marker_id in visible_ids:
        index = detected_ids.index(marker_id)
        detected = np.asarray(corners[index], dtype=np.float32).reshape(4, 2)
        position = layout["marker_positions"][str(marker_id)]
        canonical = _marker_corners(
            int(position["x"]),
            int(position["y"]),
            marker_size
        )
        src_points.extend(detected)
        dst_points.extend(canonical)
        src_centers.append(np.mean(detected, axis=0))
        dst_centers.append(np.mean(canonical, axis=0))

    src_points = np.asarray(src_points, dtype=np.float32)
    dst_points = np.asarray(dst_points, dtype=np.float32)

    homography_method = 0 if len(visible_ids) == 1 else cv2.RANSAC
    projective_h, ransac_mask = cv2.findHomography(
        src_points,
        dst_points,
        homography_method,
        3.0
    )

    if projective_h is None:
        raise RuntimeError("HOMOGRAPHY_UNSTABLE: Homography计算失败")

    def point_errors(matrix):
        projected_points = cv2.perspectiveTransform(
            src_points.reshape(-1, 1, 2),
            matrix
        ).reshape(-1, 2)
        return np.linalg.norm(projected_points - dst_points, axis=1)

    projective_errors = point_errors(projective_h)
    homography = projective_h
    geometry_model = "projective"
    affine_errors = None

    if len(visible_ids) == 3:
        affine_2x3 = cv2.getAffineTransform(
            np.asarray(src_centers, dtype=np.float32),
            np.asarray(dst_centers, dtype=np.float32)
        )
    elif len(visible_ids) == 2:
        affine_2x3, _ = cv2.estimateAffinePartial2D(
            np.asarray(src_centers, dtype=np.float32),
            np.asarray(dst_centers, dtype=np.float32),
            method=cv2.LMEDS,
            refineIters=10
        )
    elif len(visible_ids) == 1:
        affine_2x3, _ = cv2.estimateAffine2D(
            src_points,
            dst_points,
            method=cv2.LMEDS,
            refineIters=10
        )
    else:
        affine_2x3 = None

    if affine_2x3 is not None:
        affine_h = np.vstack([
            np.asarray(affine_2x3, dtype=np.float64),
            np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
        ])
        affine_errors = point_errors(affine_h)

        # Marker不齐时优先使用低自由度模型，避免局部角点误差在
        # 缺失Marker方向被投影模型放大；只有仿射拟合明显不成立时
        # 才退回完整Homography。
        if float(np.max(affine_errors)) <= affine_max_error_px:
            homography = affine_h
            geometry_model = "affine_degraded"

    if not np.all(np.isfinite(homography)):
        raise RuntimeError("HOMOGRAPHY_UNSTABLE: Homography包含非有限值")

    determinant = float(np.linalg.det(homography))
    if abs(determinant) < 1e-12:
        raise RuntimeError("HOMOGRAPHY_UNSTABLE: Homography接近奇异")

    errors = point_errors(homography)

    screen_width = int(layout["screen_width"])
    screen_height = int(layout["screen_height"])
    rectified_board = cv2.warpPerspective(
        observed_image,
        homography,
        (screen_width, screen_height),
        flags=interpolation,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255)
    )

    observed_mask = np.full(
        observed_image.shape[:2],
        255,
        dtype=np.uint8
    )
    rectified_mask = cv2.warpPerspective(
        observed_mask,
        homography,
        (screen_width, screen_height),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0
    )
    rectified_mask = np.where(
        rectified_mask >= 128,
        255,
        0
    ).astype(np.uint8)

    if mask_erode_px > 0:
        kernel_size = 2 * int(mask_erode_px) + 1
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        rectified_mask = cv2.erode(rectified_mask, kernel, iterations=1)

    content = layout["content_rect"]
    x = int(content["x"])
    y = int(content["y"])
    width = int(content["width"])
    height = int(content["height"])
    restored_content = rectified_board[y:y + height, x:x + width].copy()
    content_mask = rectified_mask[y:y + height, x:x + width].copy()
    content_coverage_ratio = float(np.mean(content_mask >= 128))
    geometry_grade, geometry_mode = _geometry_grade(visible_ids, layout)

    diagnostics = {
        "detected_marker_ids": detected_ids,
        "visible_layout_marker_ids": visible_ids,
        "missing_marker_ids": [
            marker_id for marker_id in MARKER_IDS if marker_id not in visible_ids
        ],
        "marker_count": len(visible_ids),
        "geometry_grade": geometry_grade,
        "geometry_mode": geometry_mode,
        "geometry_model": geometry_model,
        "content_coverage_ratio": content_coverage_ratio,
        "reprojection_mean_px": float(np.mean(errors)),
        "reprojection_max_px": float(np.max(errors)),
        "projective_reprojection_mean_px": float(np.mean(projective_errors)),
        "affine_reprojection_mean_px": (
            float(np.mean(affine_errors))
            if affine_errors is not None
            else None
        ),
        "homography": homography.tolist(),
        "homography_determinant": determinant,
        "ransac_inliers": (
            int(ransac_mask.sum())
            if ransac_mask is not None
            else len(src_points)
        )
    }

    return restored_content, rectified_board, content_mask, diagnostics


def show_board_fullscreen(board, window_name="Anti-screen watermark V1.8"):
    """以主显示器原生尺寸全屏显示画布，按Esc或Q退出。"""

    enable_physical_pixel_coordinates()
    screen_width, screen_height = get_primary_screen_size()
    board_height, board_width = board.shape[:2]

    if (board_width, board_height) != (screen_width, screen_height):
        raise ValueError(
            "画布尺寸与当前主显示器不一致: "
            f"board={board_width}x{board_height}, "
            f"screen={screen_width}x{screen_height}"
        )

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(
        window_name,
        cv2.WND_PROP_FULLSCREEN,
        cv2.WINDOW_FULLSCREEN
    )
    cv2.imshow(window_name, board)

    while True:
        key = cv2.waitKey(50) & 0xFF
        if key in (27, ord("q"), ord("Q")):
            break

    cv2.destroyWindow(window_name)
