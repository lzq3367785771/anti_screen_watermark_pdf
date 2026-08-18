# 文档水印发行与溯源网页 Demo

本目录独立保存网页界面和本地HTTP服务，提供两个用户入口：

- 上传PDF、填写水印号码并下载带水印PDF；
- 上传偷拍文档照片，输出溯源到的水印号码。
- 按原始文档查看全部水印版本，在线预览、下载或删除指定版本。

水印号码是面向用户的业务编号。系统仍会在内部随机生成TraceToken作为实际载荷，
并把水印号码、TraceToken和TraceID关联写入注册库。

## 文件说明

```text
web_demo/
├── server.py       本地HTTP服务、PDF发行和图片溯源接口
├── index.html      页面结构
├── styles.css      页面样式
├── app.js          上传、进度和结果交互
└── runtime/        运行时自动创建，不需要手工建立
    ├── uploads/          临时上传图片，默认处理后删除
    ├── reports/          每次溯源的完整技术报告和校正图片
    ├── pdf_inputs/       用户上传的发行源PDF
    ├── outputs/          生成的水印PDF和Manifest
    ├── document_assets/  后续照片溯源需要的参考页和水印页
    └── trash/            删除版本的本机可恢复归档
```

修改页面时只需要编辑 `index.html`、`styles.css` 和 `app.js`。算法入口仍然是
项目根目录的 `pdf_pipeline.py`。

## 启动

在 `anti_screen_watermark_pdf` 项目根目录运行：

```powershell
$PY = "D:\math\python\anti_screen_watermark\.venv\Scripts\python.exe"

& $PY .\web_demo\server.py `
  --host 127.0.0.1 `
  --port 8080 `
  --registry ".\document_registry.json"
```

浏览器访问：

```text
http://127.0.0.1:8080
```

按 `Ctrl+C` 停止服务。

## 可选参数

```text
--max-upload-mb 30     修改上传大小限制
--keep-uploads         处理结束后保留上传原图
--upload-dir PATH      修改上传目录
--report-dir PATH      修改报告目录
--trash-dir PATH       修改删除文件的本机回收目录
--debug                API错误响应中附带调试信息
```

PDF发行采用已验证的96 DPI、8×8 DCT、载荷repeat=24和同步Pilot参数。水印号码
长度为3～32位，只允许字母、数字、短横线和下划线，且在注册库中不可重复。

照片溯源支持 JPG、JPEG 和 PNG。HEIC 请先在手机或电脑中转换成高质量 JPG。

## 隐私和安全边界

- 默认只监听 `127.0.0.1`，外部设备无法直接访问。
- 上传图片默认在处理后删除。
- 上传PDF、输出PDF和参考页会保留在本机`runtime`目录，以支持后续溯源。
- 删除水印版本时会立即从有效注册候选中停用，并把PDF、Manifest和该版本水印页
  移入`runtime/trash`；原始PDF、公共参考页及同文档的其他版本不会被删除。
- 失败响应不会返回最接近的候选 TraceID 或 TraceToken。
- 网页最终结果严格采用 `trace_document_photo()` 的 `accepted`，不会自行降低门槛。
- 本页面是本地实验Demo；若要开放到局域网或互联网，需要另行加入HTTPS、登录、
  请求限流、任务队列和注册库权限隔离。
