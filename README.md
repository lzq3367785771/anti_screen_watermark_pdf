# anti_screen_watermark_pdf

这是从 `anti_screen_watermark` 清理迁移出的文档水印新工程。当前仅保留已经通过
V1.9.3 验证的图片水印基础能力，作为后续 PDF/文档页面水印、局部 Tile 和无
Marker 自同步工作的基线。

## 当前保留内容

| 文件 | 用途 |
|---|---|
| `codec.py` | TraceID、CRC16、Hamming 软判决与擦除解码 |
| `watermark.py` | 8×8/16×16 DCT 水印嵌入和软信息提取 |
| `synchronization.py` | PN 导频、尺度/平移搜索、Top-K 与注册库重排 |
| `trace_registry.py` | TraceID 注册库、合法码字缓存和分批最大似然评分 |
| `native_screen.py` | 当前屏幕画布和1～4 Marker几何兼容基线 |
| `sync_aruco.py` | ArUco检测兼容层；仅用于旧基线和实验对照 |
| `open_set_evaluation.py` | 盲检、开放集拒识和批量评估 |
| `phone_ab_experiment.py` | 当前统一命令行入口 |
| `tests/` | 不依赖照片和实验目录的注册库/开放集单元测试 |

ArUco 不是未来文档水印的必要条件。保留相关代码只是为了在开发局部无Marker
方案时，能够与已经验证的图片基线做回归对比。

## 未迁移内容

- 照片、原图、PDF和其他样本文件
- `experiments/`、`benchmark_results/`、`probe_results/`
- TraceID注册库实例和用户数据
- `.trace_cache`、`__pycache__`、虚拟环境
- 历史备份、旧版脚本、旧GUI和旧版README副本
- 依赖图片夹具的历史测试

## 验证当前基线

在项目根目录运行：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python .\phone_ab_experiment.py --help
```

## V2.0A.1 PDF完整页面闭环

新增文件：

| 文件 | 用途 |
|---|---|
| `document_registry.py` | 64 bit TraceToken、128 bit TraceID、文档与发行记录映射 |
| `pdf_pipeline.py` | PDF逐页渲染、8x8嵌入、派生PDF、数字提取和无Marker照片溯源 |

V2.0A使用140 bit文档码字：

```text
64 bit TraceToken + CRC16 -> Hamming(7,4) -> 140 bit
```

TraceToken嵌入页面，TraceID及接收对象等信息保存在文档注册库中。照片校正优先
使用页面内容的ORB特征和RANSAC单应性，页面四边形只作为后备，不需要ArUco。

V2.0A.1在完整页面照片提取中增加：

- 自动选取同一文档最近一次发行的Manifest，避免新旧DPI和repeat参数错配；
- 20%截尾均值聚合重复副本，降低屏幕摩尔纹极端值影响；
- PN导频Top-K之后使用注册库合法TraceToken进行载荷重排；
- 0.001尺度步长、0.5 px亚像素平移和联合精搜索；
- `synchronized.png`、`synchronized_mask.png`及完整重排报告。

### 环境准备

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
```

PDF渲染依赖Poppler的`pdftoppm`。如果它不在PATH中，可为命令传入：

```powershell
--poppler-bin "C:\path\to\poppler\Library\bin"
```

### 1. 生成水印PDF

输出PDF和Manifest默认与源PDF位于同一文件夹。

```powershell
python .\pdf_pipeline.py embed `
  --input ".\sample_pdf\NiMark.pdf" `
  --registry ".\document_registry.json" `
  --recipient "测试用户" `
  --session "v20a_round01"
```

生成：

```text
sample_pdf/NiMark_wm_<token>.pdf
sample_pdf/NiMark_wm_<token>.manifest.json
sample_pdf/.document_assets/<document_id>/...
```

### 2. 完整PDF数字提取

```powershell
python .\pdf_pipeline.py extract-digital `
  --pdf ".\sample_pdf\NiMark_wm_<token>.pdf" `
  --manifest ".\sample_pdf\NiMark_wm_<token>.manifest.json" `
  --registry ".\document_registry.json" `
  --report ".\trace_reports\digital.json"
```

### 3. 无ArUco完整页面照片溯源

照片中应包含完整页面或绝大部分页面，并保留足够正文/图表内容用于页面匹配。

```powershell
python .\pdf_pipeline.py trace-photo `
  --photo ".\sample_pdf\captures\page_01_photo.jpg" `
  --registry ".\document_registry.json" `
  --output-dir ".\trace_reports\page_01_photo"
```

输出目录包含校正页面、有效区域Mask和`trace_report.json`。

## V2.0A.2 局部分区同步

`trace-photo`在全局判决失败时自动启用局部分区路径：

- 保留ORB/RANSAC和V2.0A.1全局结果作为安全回退；
- Pilot只有16～47 bit可见但质量足够时，允许进入低覆盖候选重排；
- 身份变换强制加入候选，避免稀疏Pilot丢弃更好的原始ORB结果；
- 使用四个不重叠象限及上下/左右半页执行局部残差尺度和平移搜索；
- 对局部软信息做幅度归一化、Pilot质量加权和分区融合；
- 比较全局、单Tile、Tile融合及全局/局部混合结果，最终门槛保持不变；
- `trace_report.json`记录每个Tile的覆盖率、Pilot、局部变换和注册库分数。

默认每个Tile对27个残差候选执行载荷重排。可用以下参数做A/B或性能诊断：

```powershell
--local-payload-top-k 27
--no-local-partition-sync
```

V2.0A.2改善的是“仍有足够副本但局部网格失步”的照片。有效页面不足约15%～20%、
大量bit真实缺失的场景仍属于V2.0B，需要每个重叠Tile携带完整载荷和独立局部Pilot。
