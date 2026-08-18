from pathlib import Path
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
import sys

# 注册 HEIF 支持
register_heif_opener(thumbnails=False)

def convert_heic_to_jpg(input_path: Path, output_path: Path = None, quality: int = 95):
    """
    将单个 HEIC 文件转换为 JPG。
    
    Args:
        input_path: 输入 HEIC 文件路径
        output_path: 输出 JPG 路径（可选，默认为同目录下同名 .jpg）
        quality: JPG 压缩质量 (1-100)，默认 95（高质量）
    """
    if not input_path.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")
    
    if output_path is None:
        output_path = input_path.with_suffix(".jpg")
    
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with Image.open(input_path) as img:
        img.load()
        # 修正 EXIF 方向
        img = ImageOps.exif_transpose(img)
        # 转为 RGB（JPG 不支持透明通道）
        img = img.convert("RGB")
        # 保存为 JPG
        img.save(output_path, format="JPEG", quality=quality)
    
    print(f"✅ 转换成功: {output_path}")

def batch_convert(input_dir: Path, output_dir: Path = None, quality: int = 95):
    """
    批量转换目录下所有 HEIC 文件。
    
    Args:
        input_dir: 包含 HEIC 文件的目录
        output_dir: 输出目录（可选，默认与输入目录相同）
        quality: JPG 压缩质量
    """
    if not input_dir.exists():
        raise FileNotFoundError(f"目录不存在: {input_dir}")
    
    heic_files = list(input_dir.glob("*.HEIC")) + list(input_dir.glob("*.heic"))
    if not heic_files:
        print(f"⚠️ 未找到 HEIC 文件: {input_dir}")
        return
    
    print(f"📁 找到 {len(heic_files)} 个 HEIC 文件，开始转换...")
    
    for heic_path in heic_files:
        if output_dir is None:
            out_path = heic_path.with_suffix(".jpg")
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_path = output_dir / (heic_path.stem + ".jpg")
        try:
            convert_heic_to_jpg(heic_path, out_path, quality)
        except Exception as e:
            print(f"❌ 转换失败 {heic_path.name}: {e}")

if __name__ == "__main__":
    # 命令行参数解析
    if len(sys.argv) < 2:
        print("用法:")
        print("  转换单个文件: python heic_to_jpg.py <input.heic> [output.jpg] [--quality 95]")
        print("  批量转换目录: python heic_to_jpg.py --batch <input_dir> [--output <output_dir>] [--quality 95]")
        sys.exit(1)
    
    # 简单参数处理
    args = sys.argv[1:]
    if args[0] == "--batch":
        # 批量模式
        input_dir = Path(args[1])
        output_dir = None
        quality = 95
        if "--output" in args:
            out_idx = args.index("--output") + 1
            output_dir = Path(args[out_idx])
        if "--quality" in args:
            q_idx = args.index("--quality") + 1
            quality = int(args[q_idx])
        batch_convert(input_dir, output_dir, quality)
    else:
        # 单个文件模式
        input_path = Path(args[0])
        output_path = None
        quality = 95
        if len(args) > 1 and not args[1].startswith("--"):
            output_path = Path(args[1])
        if "--quality" in args:
            q_idx = args.index("--quality") + 1
            quality = int(args[q_idx])
        convert_heic_to_jpg(input_path, output_path, quality)