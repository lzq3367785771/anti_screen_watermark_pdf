# anti_screen_watermark_pdf

面向 **PDF 文档的抗屏摄数字水印与来源溯源原型系统**。

项目当前主线版本为 **V2.0A.2**，已经实现从 **PDF 水印发行 → 手机屏摄 → 页面识别 → 几何恢复 → 水印同步 → 软信息提取 → 注册库来源判定 → 水印文件管理** 的完整闭环，并提供本地 Web Demo 完成水印文件的生成、溯源与管理。

当前 PDF 主链路 **不依赖可见 ArUco Marker**。对于手机拍摄的文档照片，系统首先通过文档页面内容进行匹配和透视恢复，再利用 **PN Pilot、DCT 软信息、注册库 TraceToken 匹配以及局部分区同步** 完成最终来源判定。

---

## 1. 当前已实现功能

### 1.1 PDF 水印发行

系统支持上传任意 PDF 文档并生成对应的带水印 PDF。

发行过程如下：

```text
原始 PDF
    ↓
逐页渲染
    ↓
文档注册
    ↓
生成 TraceToken / TraceID
    ↓
TraceToken + CRC16
    ↓
Hamming(7,4)
    ↓
140 bit 文档码字
    ↓
DCT Payload + PN Pilot 逐页嵌入
    ↓
重新生成带水印 PDF
    ↓
保存 Manifest 和注册库记录
```

当前文档载荷结构：

```text
64 bit TraceToken
+ 16 bit CRC16
= 80 bit Payload

80 bit
→ Hamming(7,4)
→ 140 bit 编码码字
```

其中：

- **TraceToken**：实际嵌入 PDF 页面中的 64 bit 短载荷；
- **TraceID**：128 bit 完整追踪标识，保存在注册库中；
- **watermark_number**：用户可见的水印号码；
- **document_id**：由原始 PDF 文档内容生成的文档标识。

用户界面主要使用 `watermark_number`，内部通过：

```text
watermark_number
        ↓
TraceToken
        ↓
TraceID
        ↓
document_id / 发行记录
```

完成实际来源追踪。

---

## 2. PDF 水印嵌入方法

当前 PDF 页面采用 **8×8 DCT 水印**。

页面首先转换到 **YCrCb** 色彩空间，在亮度 **Y 通道**中进行嵌入。

每个 8×8 DCT 块使用两个中频系数：

```text
DCT[2,3]
DCT[3,2]
```

表示一个二进制比特。

当：

```text
bit = 1
```

时，使：

```text
DCT[2,3] > DCT[3,2]
```

当：

```text
bit = 0
```

时，使：

```text
DCT[2,3] < DCT[3,2]
```

系统通过：

- 密钥；
- 页面尺寸；
- 文档信息；

生成确定性的伪随机 DCT 嵌入位置，使不同页面具有独立的水印位置分布。

为增强屏摄环境下的可靠性，每个 Payload bit 会在多个不同 DCT 块中重复嵌入。

同时采用内容自适应嵌入强度：

- 接近纯白的区域降低嵌入强度；
- 低纹理区域适度降低强度；
- 高纹理区域可以提高嵌入强度；
- 在水印强度与视觉不可感知性之间进行平衡。

---

## 3. PN Pilot 同步

除 140 bit Payload 外，每页还额外嵌入一组已知的 **PN Pilot**。

Pilot 不携带用户身份信息，主要用于手机屏摄后的几何精同步。

主要处理流程：

```text
页面内容粗配准
    ↓
PN Pilot 搜索
    ↓
残余尺度修正
    ↓
X / Y 平移修正
    ↓
亚像素精搜索
```

当前同步搜索包含：

- PN Pilot Top-K 候选；
- 残余尺度搜索；
- 0.001 级尺度精搜索；
- 亚像素平移搜索；
- Payload 注册库重排；
- 最终联合候选选择。

---

## 4. 无 Marker 手机屏摄溯源

当前 PDF 主链路 **不依赖 ArUco Marker**。

对于上传的手机照片，系统会在已经登记的多个 PDF 文档及其所有页面中进行页面识别。

基本流程：

```text
手机照片
    ↓
遍历已登记文档页面
    ↓
ORB 特征检测
    ↓
特征匹配
    ↓
RANSAC Homography
    ↓
恢复到参考页面坐标系
```

当 ORB 页面匹配失败时，系统可以尝试通过页面四边形边界进行后备恢复。

因此，一个文档可以包含多页，同时注册库中也可以同时存在多个不同 PDF。

系统会首先判断：

```text
这张照片属于哪个 PDF？
        ↓
属于第几页？
```

然后再进行水印提取和来源判定。

---

## 5. 屏摄水印软信息提取

手机拍摄后，部分 DCT 块可能受到以下因素影响：

- 摩尔纹；
- 透视；
- 显示器像素网格；
- 手机传感器采样；
- 失焦；
- 局部反光；
- 裁剪；
- 二次缩放。

因此系统不只输出 0/1 硬判决，而是保留：

```text
score = DCT[2,3] - DCT[3,2]
```

作为有符号软信息。

其中：

```text
score > 0  → 更倾向 bit 1
score < 0  → 更倾向 bit 0
|score|    → 当前 bit 的置信程度
```

对于同一个 bit 的多个重复副本，当前 PDF 主线采用 **20% 截尾均值（Trimmed Mean）** 进行聚合。

该方法会去掉部分极大或极小异常值，以降低强摩尔纹产生的异常 DCT 响应对整体判决的影响。

---

## 6. 有效区域与 Erasure

系统在透视恢复后同时生成：

```text
valid_mask
```

只有实际由手机照片覆盖到的页面区域才参与水印提取。

如果某个 DCT 副本没有被真实拍摄到，则该副本不会被强制判为 0 或 1，而是作为：

```text
Erasure / None
```

处理。

当某一个 bit 的有效重复副本过少时，该 bit 也会被标记为擦除。

因此系统可以处理一定程度的：

- 页面裁剪；
- 页面不完整；
- 局部遮挡；
- 手机只拍到部分页面。

---

## 7. 注册库软最大似然溯源

如果 **Hamming + CRC** 可以直接恢复完整 TraceToken，则系统可以直接完成注册库确认。

在实际屏摄场景中，即使 CRC 无法通过，140 bit 的全局软信息仍可能具有明显的候选区分能力。

因此系统还会将当前提取到的软信息与该文档下所有已经发行的合法 TraceToken 码字进行比较。

对于每个候选 Token 计算：

- raw score；
- normalized score；
- z-score；
- hard match rate；
- 与第二候选之间的 margin。

只有候选同时达到多个可靠性阈值时才接受。

当前文档注册库默认判决条件包括：

```text
Observed bits ratio >= 0.80
Normalized score    >= 0.18
Z-score             >= 4.0
Margin Z            >= 1.0
Hard match rate     >= 0.60
```

若证据不足，系统选择 **拒绝**，而不是强制返回某个水印号码。

---

## 8. V2.0A.2 局部分区同步

完整页面经过 Homography 后，仍可能因为：

- 手机镜头畸变；
- 斜拍；
- 屏幕与相机像素网格干涉；
- 空间非均匀缩放；
- 局部 DCT 网格相位偏移；

导致不同页面位置需要略有不同的残余同步参数。

V2.0A.2 因此增加：

**Local Partition Synchronization**

页面会划分为两组局部区域。

### 8.1 四个象限

```text
quadrant_tl
quadrant_tr
quadrant_bl
quadrant_br
```

### 8.2 四个半页区域

```text
half_left
half_right
half_top
half_bottom
```

每个局部区域独立执行小范围：

```text
scale
dx
dy
```

残余搜索。

每个区域根据以下信息计算质量权重：

- Pilot 可见 bit 数；
- Pilot correlation；
- Pilot match rate。

随后系统生成多种候选：

```text
Global
Single Tile
Partition Fusion
Global + Local Blend
```

最终由注册库可靠判决选择最佳结果。

局部分区同步主要改善以下情况：

```text
页面仍有足够水印信息
+
局部几何或 DCT 网格失步
```

如果实际可见页面过小，导致大量 140 bit Payload 真实缺失，则仍无法仅靠同步恢复。

这属于后续 **V2.0B 局部完整载荷 Tile** 方案需要解决的问题。

---

## 9. Web Demo

项目当前提供本地网页：

```text
web_demo/
├── server.py
├── index.html
├── app.js
├── styles.css
└── README.md
```

Web Demo 不依赖 Flask，后端直接使用 Python：

```python
ThreadingHTTPServer
```

提供本地 HTTP 服务。

当前网页包含三个主要功能：

1. 嵌入水印；
2. 偷拍照片水印溯源；
3. 水印管理。

---

### 9.1 嵌入水印

用户可以：

```text
输入水印号码
    ↓
拖拽 / 选择 PDF
    ↓
点击“嵌入水印并生成 PDF”
    ↓
生成水印 PDF
    ↓
在线查看
    ↓
下载 PDF
```

水印号码规则：

```text
长度：3～32 字符
```

允许：

```text
A-Z
0-9
-
_
```

不允许重复。

Web Demo 当前采用针对屏幕显示实验优化的参数：

```text
DPI             = 96
DCT block       = 8×8

Payload alpha   = 72
Payload repeat  = 24

Pilot bits      = 64
Pilot repeat    = 8
Pilot alpha     = 90
```

PDF 上传限制：

```text
最大 100 MB
```

---

### 9.2 偷拍照片水印溯源

支持上传：

```text
JPG
JPEG
PNG
```

默认最大文件大小：

```text
30 MB
```

用户拖入手机拍摄照片后，网页自动执行：

```text
照片上传
    ↓
文档和页面识别
    ↓
ORB / RANSAC Homography
    ↓
PN Pilot 同步
    ↓
文档注册库重排
    ↓
必要时启用局部分区同步
    ↓
DCT Payload 软信息提取
    ↓
CRC / Registry ML 判决
    ↓
返回水印号码
```

溯源成功后网页主要显示：

- 水印号码；
- 识别页码；
- 注册库确认状态；
- 处理耗时。

技术详情中还可以查看：

- TraceID；
- TraceToken；
- Document ID；
- Observed bits；
- Erasure；
- CRC；
- Pilot correlation；
- Z-score；
- Hard Match；
- Margin Z；
- 页面匹配方法；
- 局部分区同步状态。

默认情况下，上传的照片在处理结束后会从上传目录删除。

---

### 9.3 水印管理

Web Demo 提供本地水印文件管理功能。

系统会按照原始文档对所有有效水印版本进行分组。

对于每个水印版本，可以：

- 在线查看；
- 下载；
- 删除。

删除采用 **软删除** 方式。

删除时：

```text
水印 PDF
Manifest
对应 watermarked pages
        ↓
移动到
web_demo/runtime/trash/
```

同时注册库中的该发行记录设置为：

```text
status = deleted
```

因此该水印版本以后不会再参与正常溯源判决。

删除记录仍保留必要审计信息，文件也可以从本机 `trash` 目录人工恢复。

---

## 10. 项目核心文件

| 文件 | 当前作用 |
| --- | --- |
| `pdf_pipeline.py` | PDF 水印发行、数字提取、手机照片溯源、页面匹配、V2.0A.2 局部分区同步 |
| `document_registry.py` | 文档注册、TraceToken、TraceID、水印号码和发行记录管理 |
| `watermark.py` | DCT 水印嵌入、提取、软信息和 Erasure |
| `synchronization.py` | PN Pilot、尺度/平移搜索及旧图片主线同步工具 |
| `codec.py` | CRC16、Hamming(7,4)、TraceID 及软擦除解码 |
| `web_demo/server.py` | Web Demo 本地 HTTP 后端 |
| `web_demo/index.html` | Web Demo 页面结构 |
| `web_demo/app.js` | PDF 上传、照片溯源、水印管理前端逻辑 |
| `web_demo/styles.css` | Web Demo 样式 |
| `tests/test_v20a_pdf.py` | 当前 PDF 主线单元测试和回归测试 |
| `trace_registry.py` | V1.9.x 图片 TraceID 注册库和开放集 ML 基线 |
| `open_set_evaluation.py` | V1.9.x 图片开放集评估工具 |
| `native_screen.py` | 旧图片原生屏幕 / Marker 实验基线 |
| `sync_aruco.py` | ArUco 旧实验兼容层 |
| `phone_ab_experiment.py` | V1.9.x 图片屏摄实验和 A/B 测试工具 |

其中：

```text
pdf_pipeline.py
document_registry.py
watermark.py
codec.py
web_demo/
```

构成当前 PDF 系统最主要的运行链路。

`native_screen.py` 和 `sync_aruco.py` 中的 ArUco 相关能力仅作为旧实验基线保留，当前 PDF Web Demo 主链 **不依赖 ArUco**。

---

## 11. 环境安装

推荐：

```text
Python 3.10
```

### 11.1 创建虚拟环境

```powershell
python -m venv .venv
```

### 11.2 激活虚拟环境

PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

### 11.3 安装依赖

```powershell
python -m pip install -r .\requirements.txt
```

当前主要 Python 依赖：

```text
numpy >= 2.0, < 3
opencv-contrib-python >= 4.10
Pillow >= 9.0
```

---

## 12. 安装 Poppler

PDF 页面渲染依赖：

```text
pdftoppm
```

因此系统需要安装 **Poppler**。

如果 `pdftoppm` 已加入系统 `PATH`，则无需额外设置。

也可以通过环境变量设置 Poppler 路径：

```powershell
$env:POPPLER_BIN = "C:\path\to\poppler\Library\bin"
```

命令行执行 `pdf_pipeline.py` 时也可以显式指定：

```powershell
--poppler-bin "C:\path\to\poppler\Library\bin"
```

---

## 13. 初始化文档注册库

Web Demo 启动时要求：

```text
document_registry.json
```

已经存在。

如果是第一次运行，可以在项目根目录执行：

```powershell
python -c "from document_registry import empty_document_registry, save_document_registry; save_document_registry('document_registry.json', empty_document_registry())"
```

注册库用于保存：

- documents；
- issues；
- TraceToken；
- TraceID；
- watermark_number；
- output PDF；
- Manifest；
- 发行状态。

`document_registry.json` 属于运行数据，**不应提交到公共 Git 仓库**。

---

## 14. 启动 Web Demo

进入项目目录：

```powershell
cd D:\math\python\anti_screen_watermark_pdf
```

使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe .\web_demo\server.py `
  --host 127.0.0.1 `
  --port 8080 `
  --registry ".\document_registry.json"
```

如果当前仍使用已有 Python 环境，也可以指定解释器，例如：

```powershell
$PY = "D:\math\python\anti_screen_watermark\.venv\Scripts\python.exe"

& $PY .\web_demo\server.py `
  --host 127.0.0.1 `
  --port 8080 `
  --registry ".\document_registry.json"
```

启动成功后访问：

```text
http://127.0.0.1:8080
```

停止服务：

```text
Ctrl + C
```

---

## 15. Web Demo 运行目录

默认运行数据保存在：

```text
web_demo/runtime/
├── uploads/
├── reports/
├── pdf_inputs/
├── outputs/
├── document_assets/
└── trash/
```

各目录作用如下。

### `uploads/`

临时保存手机照片。

### `reports/`

保存每次屏摄溯源产生的：

- trace_report；
- 中间图像；
- 调试结果。

### `pdf_inputs/`

保存网页上传的原始 PDF。

### `outputs/`

保存网页生成的带水印 PDF。

### `document_assets/`

保存：

- 文档参考页；
- 各水印发行版本对应页面资源。

### `trash/`

保存被用户软删除的水印版本。

这些目录均属于运行数据，默认不应该提交 Git。

---

## 16. 命令行使用

除了 Web Demo，还可以直接使用：

```text
pdf_pipeline.py
```

完成 PDF 水印发行、数字提取以及手机照片溯源。

---

### 16.1 生成水印 PDF

```powershell
python .\pdf_pipeline.py embed `
  --input ".\sample_pdf\document.pdf" `
  --registry ".\document_registry.json" `
  --watermark-number "WM-2026-0001" `
  --recipient "测试用户" `
  --session "test_session"
```

命令行 `embed` 默认参数为：

```text
DPI            = 150
alpha          = 42
repeat         = 16
pilot bits     = 64
pilot repeat   = 6
pilot alpha    = 78
```

> 注意：Web Demo 当前使用的是另一组针对现阶段屏摄实验优化的参数：
>
> ```text
> DPI             = 96
> Payload alpha   = 72
> Payload repeat  = 24
> Pilot bits      = 64
> Pilot repeat    = 8
> Pilot alpha     = 90
> ```
>
> CLI 默认参数与 Web Demo 参数不要混淆。

---

### 16.2 数字 PDF 提取

数字 PDF 提取主要用于验证生成后的 PDF 在没有经过屏摄攻击的数字环境下，能否正确恢复水印。

```powershell
python .\pdf_pipeline.py extract-digital `
  --pdf ".\sample_pdf\document_wm_WM-2026-0001.pdf" `
  --manifest ".\sample_pdf\document_wm_WM-2026-0001.manifest.json" `
  --registry ".\document_registry.json" `
  --report ".\trace_reports\digital.json"
```

输出会显示每页的：

```text
CRC
Trace match
Recovered TraceToken
```

---

### 16.3 手机照片溯源

```powershell
python .\pdf_pipeline.py trace-photo `
  --photo ".\sample_pdf\capture.jpg" `
  --registry ".\document_registry.json" `
  --output-dir ".\trace_reports\capture"
```

成功时输出：

```text
Status
Accepted
Document
Page
TraceID
TraceToken
```

`output-dir` 中会保存：

```text
rectified.png
valid_mask.png
synchronized.png
synchronized_mask.png
trace_report.json
```

这些文件可以用于检查：

- 页面透视恢复结果；
- 有效区域；
- 全局/局部同步情况；
- 最终来源判定结果。

---

### 16.4 V2.0A.2 调试参数

关闭文档注册库 Top-K 重排：

```powershell
--no-document-rerank
```

关闭局部分区同步：

```powershell
--no-local-partition-sync
```

控制局部 Payload 候选数量：

```powershell
--local-payload-top-k 27
```

控制重复分数截尾比例：

```powershell
--trim-ratio 0.20
```

这些参数主要用于：

- 算法 A/B 实验；
- 局部同步诊断；
- 性能分析；
- 回归测试。

---

## 17. 自动测试

在项目根目录运行：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

当前 PDF 测试覆盖的主要内容包括：

- 64 bit TraceToken → 140 bit 码字编解码；
- CRC 验证；
- 注册库 Token 评分；
- 用户水印号码唯一性；
- 删除后 Token 不再参与溯源；
- DCT 数字闭环；
- 20% Trimmed Mean；
- ORB 透视恢复；
- 局部分区 Mask；
- 局部分区软信息融合；
- 最新 Manifest 选择。

---

## 18. 当前系统状态

当前已经形成以下完整链路：

```text
PDF 水印发行
        ↓
多 PDF 文档注册
        ↓
多水印版本发行
        ↓
无 Marker 手机屏摄页面识别
        ↓
ORB / RANSAC 透视恢复
        ↓
PN Pilot 精同步
        ↓
DCT 软信息提取
        ↓
注册库 TraceToken ML 判决
        ↓
V2.0A.2 局部分区同步
        ↓
水印号码来源确认
        ↓
Web Demo 文件管理
```

现阶段已经形成可实际操作的 **PDF 抗屏摄水印原型系统**。

在目前进行的：

- 多 PDF；
- 不同屏摄角度；
- 局部裁剪；
- 屏摄干扰；

等实验中，系统已经验证了较好的水印溯源能力。

但当前实验仍属于 **原型验证阶段**，不代表已经完成：

- 生产级误报率验证；
- 生产级漏报率验证；
- 大规模开放集可靠性验证；
- 大规模并发服务验证。

---

## 19. 当前限制

当前主要限制包括：

1. 当实际拍摄页面区域过小时，大量 Payload bit 会真实缺失；
2. V2.0A.2 只能修复局部同步误差，无法创造不存在的水印副本；
3. 当前系统仍需要足够的页面正文、标题或图表内容进行页面识别；
4. 注册库规模和开放集误接受率仍需要更大规模实验；
5. Web Demo 当前定位为本地原型，不包含账户、权限、HTTPS、数据库和并发任务队列等正式服务能力；
6. 当前默认密钥仍存在于代码配置中，因此仓库现阶段建议保持 **Private**。

---

## 20. 下一阶段：V2.0B

V2.0B 的核心目标不是继续扩大全局搜索，而是解决：

> **任意较小局部页面仍然能够独立完成身份恢复。**

计划方向为：

```text
页面
↓
重叠 Local Tiles
↓
每个 Tile 独立携带完整短 TraceToken
↓
每个 Tile 独立 PN Pilot
↓
局部自同步
↓
单 Tile 即可完成来源判定
```

该方案将用于解决当前约 **15%～20% 以下有效页面区域**中：

```text
Observed bits 不足
```

这一根本问题。

V2.0A.2 的局部分区同步解决的是：

```text
已有 Payload
但局部同步失准
```

而 V2.0B 计划解决的是：

```text
只拍到页面较小局部
但该局部本身就拥有完整身份载荷
```

二者属于不同层次的问题。

---

## 21. 数据与 Git 管理

以下内容属于运行数据，不应提交到源码仓库：

```text
document_registry.json
web_demo/runtime/
sample_pdf/
trace_reports/
benchmark_results/
probe_results/
.trace_cache/
backups/
.venv/
__pycache__/
*.pdf
*.jpg
*.jpeg
*.png
```

建议在 `.gitignore` 中忽略这些文件和目录。

GitHub 仓库当前建议保持：

```text
Private
```

在未来公开源码前，需要进一步处理：

- 默认密钥；
- 环境变量；
- 运行注册库；
- 实验数据；
- 本地绝对路径；
- 用户发行信息；
- 调试输出；
- 测试阶段产生的水印文件。

---

## 22. 版本信息

```text
Project: anti_screen_watermark_pdf

Current PDF Pipeline:
V2.0A.2

Web Demo:
V2.0A.2
```

当前 PDF 主线：

```text
PDF Issue
→ Screen Capture
→ Page Recognition
→ Global Synchronization
→ Local Partition Synchronization
→ Soft Extraction
→ Registry Attribution
→ Watermark Management
```

---

## 23. 版本一致性说明

当前 README 与 PDF 主线版本统一为：

```text
V2.0A.2
```

需要注意，若 `pdf_pipeline.py` 中仍存在类似：

```python
argparse.ArgumentParser(
    description="PDF文档抗屏摄水印V2.0A.1"
)
```

这样的旧版本字符串，应同步修改为：

```python
argparse.ArgumentParser(
    description="PDF文档抗屏摄水印V2.0A.2"
)
```

避免：

```text
代码版本
README 版本
Web Demo 版本
```

三处显示不一致。

---

## 24. 项目定位

`anti_screen_watermark_pdf` 当前已经不再是一个仅用于为 PDF 水印做准备的迁移工程。

当前项目已经形成：

> **一个面向 PDF 文档发行、手机屏摄恢复和来源溯源的无 Marker 抗屏摄数字水印原型系统。**

系统当前重点关注以下问题：

- 水印不可感知性；
- 手机屏摄鲁棒性；
- 页面几何恢复；
- DCT 网格同步；
- 局部空间失步；
- 裁剪条件下的信息擦除；
- 多文档、多发行版本来源判定；
- 开放集条件下的拒绝机制；
- 实际水印文件发行与管理。

后续 V2.0B 将进一步从当前的：

```text
全页面分布式 Payload
```

向：

```text
局部 Tile 独立完整载荷
```

演进，以增强小范围局部拍摄场景下的来源恢复能力。