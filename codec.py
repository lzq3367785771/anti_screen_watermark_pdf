# 1.生成唯一的“身份证号”（TraceID）；
# 2. 给这个号加上“防伪印章”（CRC校验）和“纠错涂层”（Hamming纠错码），打包成水印比特流；
# 3. 在提取时，负责“拆包、纠错、验伪”，最终还原出原始的TraceID.



import binascii
import secrets


# ============================================================
# 纠错码 + 载荷编解码
#
# 从 main.py 拆出，供 real_phone_test.py 等独立复用
# ============================================================


# ============================================================
# Byte / Bit转换
# ============================================================

def generate_trace_id():
    """生成128bit随机TraceID。"""

    return secrets.token_hex(16)


def bytes_to_bits(data: bytes):
    """bytes -> bit list。"""

    bits = []

    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)

    return bits

def bits_to_bytes(bits):
    """
    bit list -> bytes

    bit数量必须是8的倍数
    """

    if len(bits) % 8 != 0:
        raise ValueError(
            f"bit长度必须是8的倍数，当前长度={len(bits)}"
        )

    result = bytearray()

    for i in range(0, len(bits), 8):

        byte_bits = bits[i:i + 8]

        value = 0

        for bit in byte_bits:
            value = (value << 1) | bit

        result.append(value)

    return bytes(result)


# ============================================================
# CRC16
# ============================================================

def calculate_crc16(data: bytes):
    """
    CRC-16-CCITT

    初始值:
    0xFFFF

    返回2字节CRC
    """

    crc = binascii.crc_hqx(
        data,
        0xFFFF
    )

    return crc.to_bytes(
        2,
        byteorder="big"
    )


def verify_payload(payload: bytes):
    """
    检查CRC是否正确
    """

    if len(payload) != 18:

        raise ValueError(
            "Payload必须为18 Byte"
        )

    trace_bytes = payload[:16]

    received_crc = payload[16:18]

    calculated_crc = calculate_crc16(
        trace_bytes
    )

    success = (
        received_crc
        ==
        calculated_crc
    )

    trace_id = trace_bytes.hex()

    return (
        success,
        trace_id,
        received_crc.hex(),
        calculated_crc.hex()
    )


def build_payload(trace_id_hex):
    """构建16 Byte TraceID + 2 Byte CRC16载荷。"""

    trace_bytes = bytes.fromhex(trace_id_hex)

    if len(trace_bytes) != 16:
        raise ValueError("TraceID必须为128bit，也就是16Byte")

    return trace_bytes + calculate_crc16(trace_bytes)


def hamming74_encode_block(data_bits):
    """把4bit数据编码为Hamming(7,4)。"""

    if len(data_bits) != 4:
        raise ValueError("Hamming输入必须为4bit")

    d1, d2, d3, d4 = data_bits
    p1 = d1 ^ d2 ^ d4
    p2 = d1 ^ d3 ^ d4
    p4 = d2 ^ d3 ^ d4

    return [p1, p2, d1, p4, d2, d3, d4]


def hamming_encode(bits):
    """对完整bit流执行Hamming(7,4)编码。"""

    if len(bits) % 4 != 0:
        raise ValueError("Hamming输入长度必须是4的倍数")

    encoded = []

    for i in range(0, len(bits), 4):
        encoded.extend(hamming74_encode_block(bits[i:i + 4]))

    return encoded


# ============================================================
# Hamming(7,4) 解码
# ============================================================

def hamming74_decode_block(code_bits):
    """
    输入:
        7bit Hamming码

    功能:
        检测并纠正一个bit错误

    返回:
        原始4bit
        错误位置
        修正后的7bit
    """

    if len(code_bits) != 7:

        raise ValueError(
            "Hamming码必须是7bit"
        )

    c = list(code_bits)

    # Syndrome
    #
    # s1检查:
    # position 1,3,5,7

    s1 = (
        c[0]
        ^ c[2]
        ^ c[4]
        ^ c[6]
    )

    # s2检查:
    # position 2,3,6,7

    s2 = (
        c[1]
        ^ c[2]
        ^ c[5]
        ^ c[6]
    )

    # s4检查:
    # position 4,5,6,7

    s4 = (
        c[3]
        ^ c[4]
        ^ c[5]
        ^ c[6]
    )

    error_position = (
        s1
        +
        2 * s2
        +
        4 * s4
    )

    # 发现错误
    if error_position != 0:

        # Hamming位置从1开始
        # Python下标从0开始
        index = error_position - 1

        # 翻转bit
        c[index] ^= 1

    # 取出真正的数据位
    data = [
        c[2],
        c[4],
        c[5],
        c[6]
    ]

    return (
        data,
        error_position,
        c
    )


# ============================================================
# 完整Hamming解码
# ============================================================

def hamming_decode(bits):
    """
    每7bit:
        ↓
    纠错
        ↓
    恢复4bit
    """

    if len(bits) % 7 != 0:

        raise ValueError(
            "Hamming编码长度必须是7的倍数"
        )

    decoded = []

    error_records = []

    for block_index, i in enumerate(
        range(
            0,
            len(bits),
            7
        )
    ):

        block = bits[
            i:i + 7
        ]

        (
            data,
            error_position,
            corrected
        ) = hamming74_decode_block(
            block
        )

        decoded.extend(data)

        if error_position != 0:

            error_records.append({
                "block": block_index,
                "error_position": error_position,
                "received": block,
                "corrected": corrected
            })

    return (
        decoded,
        error_records
    )


def hamming_soft_erasure_decode(
        soft_scores,
        min_observations=3,
        min_margin=1e-6
):
    """枚举16个合法Hamming码字，使用软相关分数处理擦除。"""

    if len(soft_scores) % 7 != 0:
        raise ValueError("Hamming软分数长度必须是7的倍数")
    if min_observations < 1 or min_observations > 7:
        raise ValueError("min_observations必须在1到7之间")
    if min_margin < 0:
        raise ValueError("min_margin不能小于0")

    codebook = []

    for value in range(16):
        data_bits = [
            (value >> shift) & 1
            for shift in range(3, -1, -1)
        ]
        codeword = hamming74_encode_block(data_bits)
        codebook.append((data_bits, codeword))

    decoded_bits = []
    block_records = []

    for block_index, start in enumerate(range(0, len(soft_scores), 7)):
        received = soft_scores[start:start + 7]
        observed_count = sum(score is not None for score in received)
        erased_positions = [
            index + 1
            for index, score in enumerate(received)
            if score is None
        ]

        if observed_count < min_observations:
            decoded_bits.extend([None, None, None, None])
            block_records.append({
                "block": block_index,
                "status": "INSUFFICIENT_OBSERVATIONS",
                "observed_count": observed_count,
                "erased_positions": erased_positions,
                "margin": None
            })
            continue

        candidates = []

        for data_bits, codeword in codebook:
            correlation = 0.0
            hard_mismatches = 0

            for expected_bit, score in zip(codeword, received):
                if score is None:
                    continue

                score = float(score)
                expected_sign = 1.0 if expected_bit == 1 else -1.0
                correlation += expected_sign * score
                received_bit = 1 if score > 0 else 0
                hard_mismatches += int(received_bit != expected_bit)

            candidates.append({
                "data_bits": data_bits,
                "codeword": codeword,
                "correlation": correlation,
                "hard_mismatches": hard_mismatches
            })

        candidates.sort(
            key=lambda item: (
                item["correlation"],
                -item["hard_mismatches"]
            ),
            reverse=True
        )
        best = candidates[0]
        second = candidates[1]
        margin = float(best["correlation"] - second["correlation"])

        if margin <= min_margin:
            decoded_bits.extend([None, None, None, None])
            status = "AMBIGUOUS"
        else:
            decoded_bits.extend(best["data_bits"])
            status = (
                "DECODED_WITH_ERASURES"
                if erased_positions
                else "DECODED"
            )

        block_records.append({
            "block": block_index,
            "status": status,
            "observed_count": observed_count,
            "erased_positions": erased_positions,
            "best_codeword": best["codeword"],
            "best_data_bits": best["data_bits"],
            "best_correlation": float(best["correlation"]),
            "second_correlation": float(second["correlation"]),
            "margin": margin,
            "hard_mismatches": int(best["hard_mismatches"])
        })

    return decoded_bits, block_records
