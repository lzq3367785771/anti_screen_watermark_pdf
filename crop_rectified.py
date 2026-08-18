#对图像裁剪，展示其在不同区域的水印溯源效果



import cv2
import sys
from pathlib import Path

source = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
output_dir.mkdir(parents=True, exist_ok=True)

image = cv2.imread(str(source), cv2.IMREAD_COLOR)
if image is None:
    raise RuntimeError(f"Cannot read: {source}")

h, w = image.shape[:2]

# 坐标均为页面宽高比例；不缩放，只裁剪
regions = {
    "center_75":  (0.067, 0.067, 0.933, 0.933),
    "left_50":    (0.000, 0.000, 0.500, 1.000),
    "right_50":   (0.500, 0.000, 1.000, 1.000),
    "top_50":     (0.000, 0.000, 1.000, 0.500),
    "bottom_50":  (0.000, 0.500, 1.000, 1.000),
    "center_35":  (0.204, 0.204, 0.796, 0.796),
    "left_top_25":     (0.000, 0.000, 0.500, 0.500),
    "right_top_25":    (0.500, 0.000, 1.000, 0.500),
    "left_bottom_25":  (0.000, 0.500, 0.500, 1.000),
    "right_bottom_25": (0.500, 0.500, 1.000, 1.000),
    "center_25":       (0.250, 0.250, 0.750, 0.750),
    "center_20":  (0.276, 0.276, 0.724, 0.724),
    "center_15":  (0.306, 0.306, 0.694, 0.694),
    "center_10":  (0.342, 0.342, 0.658, 0.658),
}

for name, (x1r, y1r, x2r, y2r) in regions.items():
    x1, x2 = round(w * x1r), round(w * x2r)
    y1, y2 = round(h * y1r), round(h * y2r)

    crop = image[y1:y2, x1:x2]
    output = output_dir / f"{name}.png"

    if not cv2.imwrite(str(output), crop):
        raise RuntimeError(f"Cannot write: {output}")

    area_ratio = crop.shape[0] * crop.shape[1] / (h * w)
    print(f"{name}: {crop.shape[1]}x{crop.shape[0]}, area={area_ratio:.2%}")