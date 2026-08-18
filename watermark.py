#水印系统的核心底层实现——DCT（离散余弦变换）域的数字水印嵌入与提取引擎
# 1. 水印嵌入原理（embed_watermark + embed_bit）
# 选择嵌入位置：通过 generate_positions 函数，用密钥 + 图片尺寸生成一个伪随机的 DCT 块序列。这确保了不同图片、不同密钥下，水印藏的位置不同，提高了安全性。
# 核心嵌入手法（embed_bit）：取一个 8x8 像素块，做 DCT 变换。利用两个特定中频系数 (2,3) 和 (3,2) 的相对大小来代表 0 或 1。
# 比特 1：强制 DCT[2,3] > DCT[3,2]（且差值至少为 alpha）
# 比特 0：强制 DCT[2,3] < DCT[3,2]
# 冗余嵌入：每个比特会重复嵌入 repeat 次（默认 16 次）到不同的 DCT 块中。这就是为什么你的日志里总是 DCT units: 4032（252 bit × 16 repeat = 4032 个块）。
# 2. 水印提取原理（extract_watermark + extract_bit）
# 同样定位到那些 DCT 块，提取 DCT[2,3] 和 DCT[3,2] 的差值作为置信度分数（score）：
# score > 0 → 判为 1
# score < 0 → 判为 0
# 中位数投票：对于重复嵌入的 16 个副本，取它们置信度分数的中位数作为最终判决。这比平均值更抗离群噪声（比如局部反光或摩尔纹导致的极端值）。
# 输出软信息：你日志中的 Confidence p50 和 signed_scores（有符号分数）就来自这里。
# 3. 针对物理屏摄的高级提取（extract_watermark_with_erasure）
# 这是代码中为实战场景专门设计的函数：
# 传入 valid_mask：手机拍照时，可能只拍到屏幕的一部分（比如边缘没拍全），这个 Mask 标记了哪些像素是“真实拍到屏幕”的。
# 擦除（Erasure）机制：如果某个 DCT 块对应的区域在照片中被遮挡或未拍到，则该块的提取结果被标记为 None（擦除），不参与投票。
# 软判决：只使用可见且未被遮挡的 DCT 块进行解码，大幅提高了物理翻拍的可靠性。


import cv2
import numpy as np
import hashlib


BLOCK_SIZE = 8


# ============================================================
# 1. 根据密钥生成固定DCT块位置
# ============================================================

def generate_positions(
        height,
        width,
        key,
        count,
        block_size=BLOCK_SIZE
):
    """
    根据key伪随机选择DCT Block。

    相同:
        图片尺寸
        key
        count

    会得到完全相同的位置。
    """

    if block_size < 4:
        raise ValueError("DCT块尺寸必须至少为4")

    block_rows = height // block_size
    block_cols = width // block_size

    positions = []

    # 避开最外围的block
    for row in range(1, block_rows - 1):
        for col in range(1, block_cols - 1):
            positions.append(
                (row, col)
            )

    if len(positions) < count:
        raise ValueError(
            f"图片DCT块不足，需要{count}个，"
            f"实际只有{len(positions)}个"
        )

    seed_suffix = (
        f"_{height}_{width}"
        if block_size == BLOCK_SIZE
        else f"_{height}_{width}_block{block_size}"
    )

    seed_data = (key + seed_suffix).encode("utf-8")

    digest = hashlib.sha256(
        seed_data
    ).digest()

    seed = int.from_bytes(
        digest[:8],
        byteorder="big"
    )

    rng = np.random.default_rng(
        seed
    )

    rng.shuffle(
        positions
    )

    return positions[:count]


# ============================================================
# 2. 在单个8x8块嵌入一个bit
# ============================================================

def embed_bit(
        block,
        bit,
        alpha=30.0
):
    """
    bit = 1:
        DCT[2,3] > DCT[3,2]

    bit = 0:
        DCT[2,3] < DCT[3,2]
    """

    block = block.astype(
        np.float32
    )

    # DCT通常先减128
    shifted = block - 128.0

    dct = cv2.dct(
        shifted
    )

    a = dct[2, 3]
    b = dct[3, 2]

    middle = (
        a + b
    ) / 2.0

    if bit == 1:

        dct[2, 3] = (
            middle
            +
            alpha / 2
        )

        dct[3, 2] = (
            middle
            -
            alpha / 2
        )

    else:

        dct[2, 3] = (
            middle
            -
            alpha / 2
        )

        dct[3, 2] = (
            middle
            +
            alpha / 2
        )

    restored = cv2.idct(
        dct
    )

    restored += 128.0

    restored = np.clip(
        restored,
        0,
        255
    )

    return restored.astype(
        np.uint8
    )


# ============================================================
# 3. 提取一个bit
# ============================================================

def extract_bit(block):
    """
    返回:
        bit
        score

    score > 0:
        1

    score < 0:
        0
    """

    block = block.astype(
        np.float32
    )

    shifted = block - 128.0

    dct = cv2.dct(
        shifted
    )

    a = dct[2, 3]
    b = dct[3, 2]

    score = float(
        a - b
    )

    bit = (
        1
        if score > 0
        else 0
    )

    return bit, score


# ============================================================
# 4. 把完整bit流嵌入图片
# ============================================================

def embed_watermark(
        image,
        bits,
        key,
        alpha=30,
        repeat=16,
        block_size=BLOCK_SIZE,
        position_offset=0
):
    """
    第二版:
        每bit重复嵌入repeat次。
    """

    if image is None:
        raise ValueError(
            "输入图片为空"
        )

    height, width = (
        image.shape[:2]
    )

    # BGR -> YCrCb
    ycrcb = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2YCrCb
    )

    y, cr, cb = cv2.split(
        ycrcb
    )

    if position_offset < 0:
        raise ValueError("position_offset不能小于0")

    total_count = position_offset + len(bits) * repeat

    positions = generate_positions(
        height,
        width,
        key,
        total_count,
        block_size=block_size
    )

    index = int(position_offset)

    for bit in bits:

        for _ in range(repeat):

            row, col = positions[index]

            y1 = row * block_size
            x1 = col * block_size

            block = y[
                y1:y1 + block_size,
                x1:x1 + block_size
            ]

            new_block = embed_bit(
                block,
                bit,
                alpha
            )

            y[
                y1:y1 + block_size,
                x1:x1 + block_size
            ] = new_block

            index += 1

    merged = cv2.merge(
        [y, cr, cb]
    )

    result = cv2.cvtColor(
        merged,
        cv2.COLOR_YCrCb2BGR
    )

    return result


# ============================================================
# 5. 从图片提取完整bit流
# ============================================================

def extract_watermark(
        image,
        bit_count,
        key,
        repeat=16,
        block_size=BLOCK_SIZE,
        return_details=False,
        position_offset=0
):
    """
    第二版:
        每bit从repeat个位置提取，使用中位数投票。
    """

    if image is None:
        raise ValueError(
            "输入图片为空"
        )

    height, width = (
        image.shape[:2]
    )

    ycrcb = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2YCrCb
    )

    y, _, _ = cv2.split(
        ycrcb
    )

    if position_offset < 0:
        raise ValueError("position_offset不能小于0")

    total_count = position_offset + bit_count * repeat

    positions = generate_positions(
        height,
        width,
        key,
        total_count,
        block_size=block_size
    )

    bits = []
    confidences = []
    signed_scores = []
    repeat_scores = []

    index = int(position_offset)

    for _ in range(bit_count):

        scores = []

        for _ in range(repeat):

            row, col = positions[index]

            y1 = row * block_size
            x1 = col * block_size

            block = y[
                y1:y1 + block_size,
                x1:x1 + block_size
            ]

            _, score = extract_bit(
                block
            )

            scores.append(score)

            index += 1

        final_score = float(
            np.median(scores)
        )

        final_bit = (
            1
            if final_score > 0
            else 0
        )

        bits.append(final_bit)

        signed_scores.append(final_score)
        repeat_scores.append([float(score) for score in scores])

        confidences.append(
            abs(final_score)
        )

    if return_details:
        details = {
            "block_size": block_size,
            "repeat": repeat,
            "signed_scores": signed_scores,
            "repeat_scores": repeat_scores
        }
        return bits, confidences, details

    return bits, confidences


def extract_watermark_with_erasure(
        image,
        valid_mask,
        bit_count,
        key,
        repeat=16,
        block_size=BLOCK_SIZE,
        min_block_coverage=0.90,
        min_valid_repeats=3,
        position_offset=0
):
    """仅使用真实可见的DCT副本，缺失bit以None和软分数None返回。"""

    if image is None:
        raise ValueError("输入图片为空")
    if valid_mask is None:
        raise ValueError("有效区域Mask为空")

    height, width = image.shape[:2]

    if valid_mask.shape[:2] != (height, width):
        raise ValueError(
            "Mask尺寸必须与内容图一致: "
            f"image={width}x{height}, "
            f"mask={valid_mask.shape[1]}x{valid_mask.shape[0]}"
        )
    if not 0.0 <= min_block_coverage <= 1.0:
        raise ValueError("min_block_coverage必须在0到1之间")
    if min_valid_repeats < 1 or min_valid_repeats > repeat:
        raise ValueError("min_valid_repeats必须在1到repeat之间")

    if valid_mask.ndim == 3:
        mask_gray = valid_mask[:, :, 0]
    else:
        mask_gray = valid_mask

    ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
    y, _, _ = cv2.split(ycrcb)
    if position_offset < 0:
        raise ValueError("position_offset不能小于0")

    total_count = position_offset + bit_count * repeat
    positions = generate_positions(
        height,
        width,
        key,
        total_count,
        block_size=block_size
    )

    bits = []
    soft_scores = []
    confidences = []
    valid_repeat_counts = []
    erasure_indices = []
    repeat_scores = []
    repeat_coverages = []
    position_index = int(position_offset)

    for bit_index in range(bit_count):
        valid_scores = []
        bit_repeat_scores = []
        bit_coverages = []

        for _ in range(repeat):
            row, col = positions[position_index]
            position_index += 1
            y1 = row * block_size
            x1 = col * block_size
            block_mask = mask_gray[
                y1:y1 + block_size,
                x1:x1 + block_size
            ]
            coverage = float(np.mean(block_mask >= 128))
            bit_coverages.append(coverage)

            if coverage < min_block_coverage:
                bit_repeat_scores.append(None)
                continue

            block = y[
                y1:y1 + block_size,
                x1:x1 + block_size
            ]
            _, score = extract_bit(block)
            score = float(score)
            valid_scores.append(score)
            bit_repeat_scores.append(score)

        valid_count = len(valid_scores)
        valid_repeat_counts.append(valid_count)
        repeat_scores.append(bit_repeat_scores)
        repeat_coverages.append(bit_coverages)

        if valid_count < min_valid_repeats:
            bits.append(None)
            soft_scores.append(None)
            confidences.append(None)
            erasure_indices.append(bit_index)
            continue

        final_score = float(np.median(valid_scores))
        bits.append(1 if final_score > 0 else 0)
        soft_scores.append(final_score)
        confidences.append(abs(final_score))

    details = {
        "block_size": int(block_size),
        "repeat": int(repeat),
        "min_block_coverage": float(min_block_coverage),
        "min_valid_repeats": int(min_valid_repeats),
        "position_offset": int(position_offset),
        "valid_repeat_counts": valid_repeat_counts,
        "erasure_indices": erasure_indices,
        "erasure_count": len(erasure_indices),
        "erasure_rate": float(len(erasure_indices) / bit_count),
        "valid_dct_unit_ratio": float(
            sum(valid_repeat_counts) / (bit_count * repeat)
        ),
        "confidences": confidences,
        "repeat_scores": repeat_scores,
        "repeat_coverages": repeat_coverages
    }

    return bits, soft_scores, details
