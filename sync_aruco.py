import cv2
import numpy as np


# ============================================================
# V1.7B ArUco Geometry Synchronization
# ============================================================

DICTIONARY_ID = cv2.aruco.DICT_4X4_50

MARKER_IDS = [0, 1, 2, 3]

MARKER_SIZE = 240

# 水印内容外增加白边
MARGIN = 300

# Marker距离整个画布边缘
EDGE_PAD = 30


# ============================================================
# 1. ArUco Dictionary
# ============================================================

def get_dictionary():

    return cv2.aruco.getPredefinedDictionary(
        DICTIONARY_ID
    )


# ============================================================
# 2. 生成单个 Marker
# ============================================================

def generate_marker(
        dictionary,
        marker_id,
        size
):

    # 新版OpenCV
    if hasattr(
        cv2.aruco,
        "generateImageMarker"
    ):

        marker = (
            cv2.aruco.generateImageMarker(
                dictionary,
                marker_id,
                size
            )
        )

    # 兼容部分旧版本
    else:

        marker = np.zeros(
            (size, size),
            dtype=np.uint8
        )

        cv2.aruco.drawMarker(
            dictionary,
            marker_id,
            size,
            marker,
            1
        )

    return marker


# ============================================================
# 3. Marker标准位置
# ============================================================

def get_marker_layout(
        content_width,
        content_height
):

    board_width = (
        content_width
        +
        2 * MARGIN
    )

    board_height = (
        content_height
        +
        2 * MARGIN
    )

    s = MARKER_SIZE
    p = EDGE_PAD

    positions = {

        # 左上
        0: (
            p,
            p
        ),

        # 右上
        1: (
            board_width
            -
            p
            -
            s,

            p
        ),

        # 右下
        2: (
            board_width
            -
            p
            -
            s,

            board_height
            -
            p
            -
            s
        ),

        # 左下
        3: (
            p,

            board_height
            -
            p
            -
            s
        )
    }

    return (
        board_width,
        board_height,
        positions
    )


# ============================================================
# 4. 获取Marker标准四角
# ============================================================

def marker_corners_from_position(
        x,
        y
):

    s = MARKER_SIZE

    return np.float32([
        [x, y],
        [x + s - 1, y],
        [x + s - 1, y + s - 1],
        [x, y + s - 1]
    ])


# ============================================================
# 5. 创建带四角Marker的同步画布
# ============================================================

def create_sync_board(
        watermarked_image
):

    if watermarked_image is None:

        raise ValueError(
            "watermarked_image为空"
        )

    height, width = (
        watermarked_image.shape[:2]
    )

    (
        board_width,
        board_height,
        positions
    ) = get_marker_layout(
        width,
        height
    )

    # 白色画布
    board = np.full(
        (
            board_height,
            board_width,
            3
        ),
        255,
        dtype=np.uint8
    )

    # ----------------------------------------
    # 中间放水印图片
    # ----------------------------------------

    board[
        MARGIN:MARGIN + height,
        MARGIN:MARGIN + width
    ] = watermarked_image

    dictionary = get_dictionary()

    # ----------------------------------------
    # 放置4个ArUco
    # ----------------------------------------

    for marker_id in MARKER_IDS:

        marker = generate_marker(
            dictionary,
            marker_id,
            MARKER_SIZE
        )

        marker_bgr = cv2.cvtColor(
            marker,
            cv2.COLOR_GRAY2BGR
        )

        x, y = positions[
            marker_id
        ]

        board[
            y:y + MARKER_SIZE,
            x:x + MARKER_SIZE
        ] = marker_bgr

    return board


# ============================================================
# 6. 自动检测ArUco
# ============================================================

def detect_markers(image):

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY
    )

    dictionary = (
        get_dictionary()
    )

    parameters = (
        cv2.aruco.DetectorParameters()
    )

    # 亚像素角点优化
    if hasattr(
        cv2.aruco,
        "CORNER_REFINE_SUBPIX"
    ):

        parameters.cornerRefinementMethod = (
            cv2.aruco.CORNER_REFINE_SUBPIX
        )

    # OpenCV新版
    if hasattr(
        cv2.aruco,
        "ArucoDetector"
    ):

        detector = (
            cv2.aruco.ArucoDetector(
                dictionary,
                parameters
            )
        )

        (
            corners,
            ids,
            rejected
        ) = detector.detectMarkers(
            gray
        )

    # 旧版本兼容
    else:

        (
            corners,
            ids,
            rejected
        ) = cv2.aruco.detectMarkers(
            gray,
            dictionary,
            parameters=parameters
        )

    return (
        corners,
        ids,
        rejected
    )


# ============================================================
# 7. 自动Homography校正
# ============================================================

def rectify_with_aruco_precise(
        observed_image,
        content_width,
        content_height
):

    (
        corners,
        ids,
        rejected
    ) = detect_markers(
        observed_image
    )

    if ids is None:

        raise RuntimeError(
            "没有检测到任何ArUco Marker"
        )

    detected_ids = (
        ids.flatten().tolist()
    )

    print(
        "Detected Marker IDs:",
        detected_ids
    )

    missing = [
        marker_id
        for marker_id in MARKER_IDS
        if marker_id not in detected_ids
    ]

    if missing:

        raise RuntimeError(
            f"Marker缺失: {missing}"
        )

    (
        board_width,
        board_height,
        positions
    ) = get_marker_layout(
        content_width,
        content_height
    )

    # ----------------------------------------
    # 收集：
    #
    # 手机/攻击图中的Marker坐标
    #
    #            ↓
    #
    # 标准画布中的Marker坐标
    # ----------------------------------------

    src_points = []

    dst_points = []

    for marker_id in MARKER_IDS:

        index = detected_ids.index(
            marker_id
        )

        detected = np.asarray(
            corners[index],
            dtype=np.float32
        ).reshape(
            4,
            2
        )

        x, y = positions[
            marker_id
        ]

        canonical = (
            marker_corners_from_position(
                x,
                y
            )
        )

        # 使用每个Marker的4个角
        #
        # 共4个Marker × 4角
        #
        # = 16个对应点

        src_points.extend(
            detected
        )

        dst_points.extend(
            canonical
        )

    src_points = np.asarray(
        src_points,
        dtype=np.float32
    )

    dst_points = np.asarray(
        dst_points,
        dtype=np.float32
    )

    # ------------------------------------------------
    # distorted board → canonical board
    # ------------------------------------------------

    H, mask = cv2.findHomography(
        src_points,
        dst_points,
        0
    )

    if H is None:
        raise RuntimeError(
            "Homography计算失败"
        )

    mean_error, max_error = (
        calculate_reprojection_error(
            src_points,
            dst_points,
            H
        )
    )

    print(
        f"Homography reprojection "
        f"mean: {mean_error:.3f}px"
    )

    print(
        f"Homography reprojection "
        f"max : {max_error:.3f}px"
    )

    # 一次性恢复整个board
    rectified_board = cv2.warpPerspective(
        observed_image,
        H,
        (
            board_width,
            board_height
        ),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(
            255,
            255,
            255
        )
    )

    # ------------------------------------------------
    # 再按照创建board时完全相同的位置裁剪
    # ------------------------------------------------

    restored_content = rectified_board[
        MARGIN:
        MARGIN + content_height,

        MARGIN:
        MARGIN + content_width
    ].copy()

    return (
        restored_content,
        rectified_board,
        H
    )


# ============================================================
# 8.5 重投影误差
# ============================================================

def calculate_reprojection_error(
        src_points,
        dst_points,
        H
):

    src = src_points.reshape(
        -1,
        1,
        2
    ).astype(np.float32)

    projected = cv2.perspectiveTransform(
        src,
        H
    ).reshape(
        -1,
        2
    )

    errors = np.linalg.norm(
        projected - dst_points,
        axis=1
    )

    mean_error = float(
        np.mean(errors)
    )

    max_error = float(
        np.max(errors)
    )

    return mean_error, max_error


# ============================================================
# 9. 从标记提取内容四角
# ============================================================

def get_content_quad_from_markers(
        corners,
        ids
):
    if ids is None:
        raise RuntimeError(
            "没有检测到ArUco Marker"
        )

    id_list = ids.flatten().tolist()

    for required_id in [0, 1, 2, 3]:
        if required_id not in id_list:
            raise RuntimeError(
                f"缺少Marker {required_id}"
            )

    marker_dict = {}

    for i, marker_id in enumerate(id_list):
        pts = np.asarray(
            corners[i],
            dtype=np.float32
        ).reshape(4, 2)

        marker_dict[marker_id] = pts

    # ArUco角点顺序：
    #
    # 0 = 左上 TL
    # 1 = 右上 TR
    # 2 = 右下 BR
    # 3 = 左下 BL

    top_left = marker_dict[0][2]  # ID0 的右下角
    top_right = marker_dict[1][3]  # ID1 的左下角
    bottom_right = marker_dict[2][0]  # ID2 的左上角
    bottom_left = marker_dict[3][1]  # ID3 的右上角

    return np.float32([
        top_left,
        top_right,
        bottom_right,
        bottom_left
    ])


# ============================================================
# 10. 直接恢复内容区域
# ============================================================

def rectify_content_directly(
        observed_image,
        content_width,
        content_height
):
    corners, ids, rejected = (
        detect_markers(
            observed_image
        )
    )

    if ids is None:
        raise RuntimeError(
            "没有检测到Marker"
        )

    print(
        "Detected Marker IDs:",
        ids.flatten().tolist()
    )

    src = get_content_quad_from_markers(
        corners,
        ids
    )

    dst = np.float32([
        [0, 0],
        [content_width - 1, 0],
        [
            content_width - 1,
            content_height - 1
        ],
        [0, content_height - 1]
    ])

    H = cv2.getPerspectiveTransform(
        src,
        dst
    )

    restored = cv2.warpPerspective(
        observed_image,
        H,
        (
            content_width,
            content_height
        ),
        flags=cv2.INTER_LINEAR
    )

    return restored, H


# ============================================================
# 8. 模拟手机透视拍摄
# ============================================================

def simulate_camera_view(
        board,
        strength=0.08
):

    height, width = (
        board.shape[:2]
    )

    dx = int(
        width * strength
    )

    dy = int(
        height * strength
    )

    src = np.float32([
        [0, 0],
        [width - 1, 0],
        [width - 1, height - 1],
        [0, height - 1]
    ])

    # 模拟斜拍
    dst = np.float32([

        [
            dx,
            dy
        ],

        [
            width - 1 - dx,
            int(dy * 0.25)
        ],

        [
            width - 1 - int(dx * 0.3),
            height - 1 - dy
        ],

        [
            int(dx * 0.25),
            height - 1 - int(dy * 0.3)
        ]
    ])

    H = cv2.getPerspectiveTransform(
        src,
        dst
    )

    attacked = cv2.warpPerspective(
        board,
        H,
        (
            width,
            height
        ),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(
            80,
            80,
            80
        )
    )

    return attacked