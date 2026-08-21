const $ = (selector) => document.querySelector(selector);

const elements = {
  serviceState: $("#serviceState"),
  serviceText: $("#serviceText"),

  embedModeButton: $("#embedModeButton"),
  traceModeButton: $("#traceModeButton"),
  manageModeButton: $("#manageModeButton"),

  embedPanel: $("#embedPanel"),
  tracePanel: $("#tracePanel"),
  managePanel: $("#managePanel"),

  workspace: $(".workspace"),
  processCard: $("#processCard"),

  refreshLibrary: $("#refreshLibrary"),
  libraryState: $("#libraryState"),
  documentLibrary: $("#documentLibrary"),

  watermarkNumber: $("#watermarkNumber"),

// 保留现有 pdf* DOM 名称以避免无必要的前端结构变更。
// 实际上传逻辑已经同时支持 PDF 和 PPTX。
  pdfInput: $("#pdfInput"),
  pdfDropZone: $("#pdfDropZone"),
  pdfPreviewPanel: $("#pdfPreviewPanel"),
  pdfFileName: $("#pdfFileName"),
  pdfFileMeta: $("#pdfFileMeta"),
  documentBadge: $(".pdf-badge"),
  removePdf: $("#removePdf"),

  embedError: $("#embedError"),
  embedButton: $("#embedButton"),

  fileInput: $("#fileInput"),
  dropZone: $("#dropZone"),
  previewPanel: $("#previewPanel"),
  previewImage: $("#previewImage"),
  fileName: $("#fileName"),
  fileMeta: $("#fileMeta"),
  removeFile: $("#removeFile"),
  uploadError: $("#uploadError"),
  traceButton: $("#traceButton"),

  processHeading: $("#processHeading"),
  processItems: [
    ...document.querySelectorAll(
      "[data-process-step]"
    ),
  ],

  estimateTitle: $("#estimateTitle"),
  estimateText: $("#estimateText"),

  processingCard: $("#processingCard"),
  processingLabel: $("#processingLabel"),
  processingTitle: $("#processingTitle"),
  processingDescription: $("#processingDescription"),
  elapsedTime: $("#elapsedTime"),

  resultCard: $("#resultCard"),
  resultIcon: $("#resultIcon"),
  resultLabel: $("#resultLabel"),
  resultTitle: $("#resultTitle"),
  resultDescription: $("#resultDescription"),

  successDetails: $("#successDetails"),

  primaryResultLabel: $("#primaryResultLabel"),
  primaryResultValue: $("#primaryResultValue"),
  copyPrimary: $("#copyPrimary"),

  detailOneLabel: $("#detailOneLabel"),
  detailOneValue: $("#detailOneValue"),
  detailTwoLabel: $("#detailTwoLabel"),
  detailTwoValue: $("#detailTwoValue"),
  detailThreeLabel: $("#detailThreeLabel"),
  detailThreeValue: $("#detailThreeValue"),

  elapsedValue: $("#elapsedValue"),

  suggestionPanel: $("#suggestionPanel"),
  suggestionList: $("#suggestionList"),

  technicalDetails: $("#technicalDetails"),
  technicalGrid: $("#technicalGrid"),

  previewButton: $("#previewButton"),
  downloadButton: $("#downloadButton"),
  retryButton: $("#retryButton"),
};


// ========================================================
// Limits / Types
// ========================================================

const IMAGE_MAX = 30 * 1024 * 1024;
const DOCUMENT_MAX = 100 * 1024 * 1024;

const WATERMARK_NUMBER =
  /^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$/;

const DOCUMENT_TYPES = {
  pdf: {
    suffix: ".pdf",
    mime: "application/pdf",
    label: "PDF",
    unit: "页",
  },

  pptx: {
    suffix: ".pptx",
    mime:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    label: "PPTX",
    unit: "张幻灯片",
  },

  docx: {
    suffix: ".docx",
    mime:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    label: "DOCX",
    unit: "页",
  },
};


// ========================================================
// Workflow descriptions
// ========================================================

const workflows = {
  embed: {
    heading: "自动发行流程",
    estimate: "预计1～3分钟",
    estimateText:
      "PDF页数或PPT幻灯片数量越多，渲染和嵌入耗时越长",

    stages: [
      [
        "正在渲染文档内容",
        "正在读取原始文档并生成稳定的参考页面。",
        "文档内容渲染",
        "建立页面或幻灯片参考图",
      ],

      [
        "正在生成专属水印",
        "正在为水印号码创建内部载荷和同步Pilot。",
        "生成内部载荷",
        "水印号码映射到安全Token",
      ],

      [
        "正在嵌入水印",
        "正在向各页面或幻灯片加入DCT载荷和同步信息。",
        "逐单元嵌入水印",
        "载荷与同步信息自适应嵌入",
      ],

      [
        "正在生成输出文档",
        "正在重建带水印文档并写入本地注册库。",
        "登记并生成文档",
        "保存发行记录和下载文件",
      ],
    ],
  },

  trace: {
    heading: "自动溯源流程",
    estimate: "预计20～40秒",
    estimateText:
      "局部照片可能需要更多候选搜索",

    stages: [
      [
        "正在识别文档页面",
        "正在将照片与已登记文档页面进行内容匹配。",
        "页面内容匹配",
        "识别文档和对应页码",
      ],

      [
        "正在校正拍摄几何",
        "正在修正透视、尺度和局部分区DCT相位。",
        "几何与局部同步",
        "修正透视、尺度和DCT相位",
      ],

      [
        "正在提取水印信息",
        "正在聚合有效区域内的水印软信息。",
        "水印软信息提取",
        "融合有效区域中的水印副本",
      ],

      [
        "正在执行可靠判决",
        "正在核验CRC、注册库置信度和候选间隔。",
        "注册库可靠判决",
        "验证CRC、置信度和候选间隔",
      ],
    ],
  },
};


// ========================================================
// State
// ========================================================

let activeMode = "embed";

let selectedDocument = null;
let selectedDocumentType = null;

let selectedImage = null;
let previewUrl = null;

let stageTimer = null;
let elapsedTimer = null;

let processingStarted = 0;


// ========================================================
// Generic helpers
// ========================================================

function formatBytes(bytes) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 ** 2) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(
    bytes / 1024 ** 2
  ).toFixed(1)} MB`;
}


function formatPercent(value) {
  if (
    value === null
    || value === undefined
  ) {
    return "—";
  }

  return `${
    (
      Number(value)
      * 100
    ).toFixed(1)
  }%`;
}


function formatSeconds(ms) {
  if (
    ms === null
    || ms === undefined
  ) {
    return "—";
  }

  return `${
    (
      Number(ms)
      / 1000
    ).toFixed(1)
  }秒`;
}


function setError(
  element,
  message = ""
) {
  element.textContent = message;
  element.hidden = !message;
}


function resetProcessSteps() {
  elements.processItems.forEach(
    (item) => {
      item.classList.remove(
        "is-active",
        "is-complete"
      );
    }
  );
}


// ========================================================
// Document type helpers
// ========================================================

function getDocumentType(file) {
  if (!file || !file.name) {
    return null;
  }

  const name = file.name.toLowerCase();

  if (name.endsWith(".pdf")) {
    return "pdf";
  }

  if (name.endsWith(".pptx")) {
    return "pptx";
  }

  if (name.endsWith(".docx")) {
    return "docx";
  }

  return null;
}


function getDocumentMime(
  type,
  file
) {
  if (
    type
    && DOCUMENT_TYPES[type]
  ) {
    return DOCUMENT_TYPES[type].mime;
  }

  return (
    file?.type
    || "application/octet-stream"
  );
}


function getDocumentLabel(type) {
  return (
    DOCUMENT_TYPES[type]?.label
    || String(
      type
      || "DOC"
    ).toUpperCase()
  );
}


function getDocumentUnit(
  sourceType,
  renderUnitType
) {
  if (
    renderUnitType === "slide"
    || sourceType === "pptx"
  ) {
    return "张幻灯片";
  }

  return "页";
}


// ========================================================
// Embed readiness
// ========================================================

function updateEmbedReady() {
  const numberOk =
    WATERMARK_NUMBER.test(
      elements.watermarkNumber
        .value
        .trim()
    );

  elements.embedButton.disabled =
    !selectedDocument
    || !numberOk;
}


// ========================================================
// Mode switching
// ========================================================

function selectMode(mode) {
  if (
    stageTimer
    || elapsedTimer
  ) {
    return;
  }

  activeMode = mode;

  elements.embedPanel.hidden =
    mode !== "embed";

  elements.tracePanel.hidden =
    mode !== "trace";

  elements.managePanel.hidden =
    mode !== "manage";

  elements.embedModeButton
    .classList
    .toggle(
      "is-active",
      mode === "embed"
    );

  elements.traceModeButton
    .classList
    .toggle(
      "is-active",
      mode === "trace"
    );

  elements.manageModeButton
    .classList
    .toggle(
      "is-active",
      mode === "manage"
    );

  elements.processCard.hidden =
    mode === "manage";

  elements.workspace
    .classList
    .toggle(
      "is-manage",
      mode === "manage"
    );

  if (mode === "manage") {
    resetProcessSteps();

    elements.resultCard.hidden = true;

    loadWatermarkLibrary();

    return;
  }

  const flow = workflows[mode];

  elements.processHeading.textContent =
    flow.heading;

  elements.estimateTitle.textContent =
    flow.estimate;

  elements.estimateText.textContent =
    flow.estimateText;

  elements.processItems.forEach(
    (item, index) => {
      item
        .querySelector("strong")
        .textContent =
          flow.stages[index][2];

      item
        .querySelector("small")
        .textContent =
          flow.stages[index][3];
    }
  );

  resetProcessSteps();

  elements.resultCard.hidden = true;
}


// ========================================================
// Document selection
// ========================================================

function clearDocument() {
  selectedDocument = null;
  selectedDocumentType = null;

  elements.pdfInput.value = "";

  elements.pdfPreviewPanel.hidden = true;
  elements.pdfDropZone.hidden = false;

  if (elements.documentBadge) {
    elements.documentBadge.textContent = "DOC";
  }

  setError(
    elements.embedError
  );

  updateEmbedReady();
}


function selectDocument(file) {
  if (!file) {
    return;
  }

  if (file.size === 0) {
    setError(
      elements.embedError,
      "文档文件为空"
    );

    return;
  }

  if (
    file.size
    > DOCUMENT_MAX
  ) {
    setError(
      elements.embedError,
      "文档超过100 MB，请选择更小的文件"
    );

    return;
  }

  const type =
    getDocumentType(file);

  if (!type) {
    setError(
      elements.embedError,
      "仅支持PDF、PPTX和DOCX文件"
    );

    return;
  }

  selectedDocument = file;
  selectedDocumentType = type;

  setError(
    elements.embedError
  );

  elements.pdfFileName.textContent =
    file.name;

  elements.pdfFileMeta.textContent =
    `${formatBytes(file.size)} · ${getDocumentLabel(type)} · 等待嵌入`;

  if (elements.documentBadge) {
    elements.documentBadge.textContent =
      getDocumentLabel(type);
  }

  elements.pdfDropZone.hidden = true;
  elements.pdfPreviewPanel.hidden = false;

  elements.resultCard.hidden = true;

  updateEmbedReady();
}


// ========================================================
// Image selection
// ========================================================

function clearImage() {
  selectedImage = null;

  elements.fileInput.value = "";

  elements.previewPanel.hidden = true;
  elements.dropZone.hidden = false;

  elements.traceButton.disabled = true;

  setError(
    elements.uploadError
  );

  if (previewUrl) {
    URL.revokeObjectURL(
      previewUrl
    );
  }

  previewUrl = null;
}


function selectImage(file) {
  if (!file) {
    return;
  }

  if (file.size === 0) {
    setError(
      elements.uploadError,
      "图片文件为空"
    );

    return;
  }

  if (
    file.size
    > IMAGE_MAX
  ) {
    setError(
      elements.uploadError,
      "图片超过30 MB，请选择更小的原始照片"
    );

    return;
  }

  if (
    ![
      "image/jpeg",
      "image/png",
    ].includes(file.type)
    && !/\.(jpe?g|png)$/i
      .test(file.name)
  ) {
    setError(
      elements.uploadError,
      "仅支持JPG、JPEG和PNG图片"
    );

    return;
  }

  clearImage();

  selectedImage = file;

  previewUrl =
    URL.createObjectURL(
      file
    );

  elements.previewImage.src =
    previewUrl;

  elements.fileName.textContent =
    file.name;

  elements.fileMeta.textContent =
    formatBytes(
      file.size
    );

  elements.previewImage.onload =
    () => {
      elements.fileMeta.textContent =
        `${
          elements.previewImage
            .naturalWidth
        } × ${
          elements.previewImage
            .naturalHeight
        } · ${
          formatBytes(
            file.size
          )
        }`;
    };

  elements.dropZone.hidden = true;
  elements.previewPanel.hidden = false;

  elements.traceButton.disabled = false;

  elements.resultCard.hidden = true;
}


// ========================================================
// Processing animation
// ========================================================

function setProcessStage(index) {
  elements.processItems.forEach(
    (item, i) => {
      item.classList.toggle(
        "is-complete",
        i < index
      );

      item.classList.toggle(
        "is-active",
        i === index
      );
    }
  );

  const stage =
    workflows[activeMode]
      .stages[index];

  elements.processingTitle.textContent =
    stage[0];

  elements.processingDescription.textContent =
    stage[1];
}


function startProcessing() {
  elements.processingCard.hidden =
    false;

  elements.resultCard.hidden =
    true;

  elements.embedButton.disabled =
    true;

  elements.traceButton.disabled =
    true;

  elements.removePdf.disabled =
    true;

  elements.removeFile.disabled =
    true;

  elements.processingLabel.textContent =
    activeMode === "embed"
      ? "ISSUING"
      : "ANALYZING";

  resetProcessSteps();
  setProcessStage(0);

  processingStarted =
    Date.now();

  let stage = 0;

  stageTimer =
    window.setInterval(
      () => {
        stage = Math.min(
          stage + 1,
          3
        );

        setProcessStage(
          stage
        );
      },

      activeMode === "embed"
        ? 15000
        : 6500
    );

  elapsedTimer =
    window.setInterval(
      () => {
        const seconds =
          Math.floor(
            (
              Date.now()
              - processingStarted
            )
            / 1000
          );

        elements.elapsedTime.textContent =
          `${
            String(
              Math.floor(
                seconds / 60
              )
            ).padStart(
              2,
              "0"
            )
          }:${
            String(
              seconds % 60
            ).padStart(
              2,
              "0"
            )
          }`;
      },

      500
    );

  elements.processingCard
    .scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
}


function stopProcessing() {
  window.clearInterval(
    stageTimer
  );

  window.clearInterval(
    elapsedTimer
  );

  stageTimer = null;
  elapsedTimer = null;

  elements.processingCard.hidden =
    true;

  elements.processItems.forEach(
    (item) => {
      item.classList.remove(
        "is-active"
      );

      item.classList.add(
        "is-complete"
      );
    }
  );

  elements.removePdf.disabled =
    false;

  elements.removeFile.disabled =
    false;

  elements.traceButton.disabled =
    !selectedImage;

  updateEmbedReady();
}


// ========================================================
// Technical information
// ========================================================

function addTechnicalItem(
  label,
  value
) {
  if (
    value === undefined
    || value === null
    || value === ""
  ) {
    return;
  }

  const item =
    document.createElement(
      "div"
    );

  const name =
    document.createElement(
      "span"
    );

  const content =
    document.createElement(
      "strong"
    );

  name.textContent = label;
  content.textContent =
    String(value);

  item.append(
    name,
    content
  );

  elements.technicalGrid.append(
    item
  );
}


function renderTechnical(data) {
  elements.technicalGrid
    .replaceChildren();

  const technical =
    data.technical
    || {};

  if (
    data.result
    === "EMBED_SUCCESS"
  ) {
    addTechnicalItem(
      "文档类型",
      data.document_type
        || data.source_type
    );

    addTechnicalItem(
      "TraceID",
      data.trace_id
    );

    addTechnicalItem(
      "TraceToken",
      data.trace_token
    );

    addTechnicalItem(
      "文档编号",
      data.document_id
    );

    addTechnicalItem(
      "渲染单元",
      data.render_unit_type
    );

    addTechnicalItem(
      "DPI",
      technical.dpi
    );

    addTechnicalItem(
      "DCT块",
      technical.block_size
        ? `${technical.block_size}×${technical.block_size}`
        : null
    );

    addTechnicalItem(
      "载荷重复",
      technical.payload_repeat
    );

    addTechnicalItem(
      "嵌入强度",
      technical.payload_alpha
    );

    addTechnicalItem(
      "Manifest",
      technical.manifest
    );

  } else {
    addTechnicalItem(
      "TraceID",
      data.trace_id
    );

    addTechnicalItem(
      "TraceToken",
      data.trace_token
    );

    addTechnicalItem(
      "文档编号",
      data.document_id
    );

    addTechnicalItem(
      "判决状态",
      technical.decision_status
    );

    addTechnicalItem(
      "有效页面覆盖",
      formatPercent(
        technical.page_coverage
      )
    );

    addTechnicalItem(
      "观察比特",
      technical.observed_bits
        === undefined
        ? null
        : `${technical.observed_bits}/140`
    );

    addTechnicalItem(
      "擦除比特",
      technical.erasures
    );

    addTechnicalItem(
      "CRC",
      technical.crc_pass
        ? "PASS"
        : "未通过"
    );

    addTechnicalItem(
      "Pilot相关性",
      technical.pilot_correlation
    );

    addTechnicalItem(
      "z_score",
      technical.z_score
    );

    addTechnicalItem(
      "Hard Match",
      formatPercent(
        technical.hard_match_rate
      )
    );

    addTechnicalItem(
      "Margin Z",
      technical.margin_z
    );

    addTechnicalItem(
      "页面匹配",
      technical.alignment_method
    );

    addTechnicalItem(
      "局部同步",
      technical.local_sync_status
    );
  }

  elements.technicalDetails.hidden =
    elements.technicalGrid
      .childElementCount === 0;

  elements.technicalDetails.open =
    false;
}


// ========================================================
// Result rendering
// ========================================================

function renderSuccess(data) {
  const isEmbed =
    data.result
    === "EMBED_SUCCESS";

  elements.resultCard.className =
    "result-card is-success";

  elements.resultIcon.textContent =
    "✓";

  if (isEmbed) {
    const documentType =
      (
        data.document_type
        || data.source_type
        || "DOCUMENT"
      ).toUpperCase();

    elements.resultLabel.textContent =
      `${documentType} READY`;

  } else {
    elements.resultLabel.textContent =
      "TRACE CONFIRMED";
  }

  elements.resultTitle.textContent =
    data.message;

  elements.resultDescription.textContent =
    data.description;

  elements.successDetails.hidden =
    false;

  elements.suggestionPanel.hidden =
    true;

  elements.primaryResultLabel.textContent =
    "水印号码";

  elements.primaryResultValue.textContent =
    data.watermark_number
    || "历史水印（未设置号码）";

  if (isEmbed) {
    elements.detailOneLabel.textContent =
      "输出文件";

    elements.detailOneValue.textContent =
      data.file_name || "—";

    const unit =
      getDocumentUnit(
        data.source_type,
        data.render_unit_type
      );

    elements.detailTwoLabel.textContent =
      unit === "张幻灯片"
        ? "幻灯片数量"
        : "文档页数";

    elements.detailTwoValue.textContent =
      data.page_count
        ? `${data.page_count} ${unit}`
        : "—";

    elements.detailThreeLabel.textContent =
      "保存状态";

    elements.detailThreeValue.textContent =
      "已登记";

  } else {
    elements.detailOneLabel.textContent =
      "识别页码";

    elements.detailOneValue.textContent =
      data.page_index
        ? `第 ${data.page_index} 页`
        : "—";

    elements.detailTwoLabel.textContent =
      "识别结果";

    elements.detailTwoValue.textContent =
      "注册库已确认";

    elements.detailThreeLabel.textContent =
      "水印号码状态";

    elements.detailThreeValue.textContent =
      data.watermark_number
        ? "已登记"
        : "历史记录";
  }

  elements.elapsedValue.textContent =
    formatSeconds(
      data.server_elapsed_ms
    );

  // ----------------------------------------------
  // Download
  // ----------------------------------------------

  if (
    isEmbed
    && data.download_url
  ) {
    elements.downloadButton.hidden =
      false;

    elements.downloadButton.href =
      data.download_url;

    if (data.file_name) {
      elements.downloadButton.download =
        data.file_name;
    }

  } else {
    elements.downloadButton.hidden =
      true;

    elements.downloadButton
      .removeAttribute("href");

    elements.downloadButton
      .removeAttribute("download");
  }

  // ----------------------------------------------
  // Preview
  //
  // PDF 有 preview_url。
  // PPTX 后端返回 null，因此保持隐藏。
  // ----------------------------------------------

  if (
    isEmbed
    && data.preview_url
  ) {
    elements.previewButton.hidden =
      false;

    elements.previewButton.href =
      data.preview_url;

    elements.previewButton.textContent =
      "在线查看 PDF";

  } else {
    elements.previewButton.hidden =
      true;

    elements.previewButton
      .removeAttribute("href");
  }

  renderTechnical(data);
}


function renderRejected(
  data,
  isError = false
) {
  elements.resultCard.className =
    `result-card ${
      isError
        ? "is-error"
        : "is-rejected"
    }`;

  elements.resultIcon.textContent =
    "!";

  elements.resultLabel.textContent =
    isError
      ? "PROCESSING ERROR"
      : "NOT CONFIRMED";

  elements.resultTitle.textContent =
    data.message
    || "操作未完成";

  elements.resultDescription.textContent =
    data.description
    || "请检查文件后重新尝试。";

  elements.successDetails.hidden =
    true;

  elements.downloadButton.hidden =
    true;

  elements.previewButton.hidden =
    true;

  elements.downloadButton
    .removeAttribute("href");

  elements.previewButton
    .removeAttribute("href");

  elements.suggestionList
    .replaceChildren();

  const suggestions =
    data.suggestions
    || (
      isError
        ? [
            "检查文件格式和大小",
            "确认本地服务仍在运行",
            "更换水印号码后重试",
          ]
        : []
    );

  suggestions.forEach(
    (text) => {
      const li =
        document.createElement(
          "li"
        );

      li.textContent = text;

      elements.suggestionList
        .append(li);
    }
  );

  elements.suggestionPanel.hidden =
    suggestions.length === 0;

  renderTechnical(data);
}


function showResult(
  data,
  responseOk
) {
  if (
    (
      data.accepted
      && data.result
        === "TRACE_SUCCESS"
    )
    || data.result
      === "EMBED_SUCCESS"
  ) {
    renderSuccess(data);

  } else {
    renderRejected(
      data,
      !responseOk
      || [
        "PROCESSING_ERROR",
        "INVALID_UPLOAD",
        "EMBED_REJECTED",
        "DOCUMENT_TYPE_NOT_IMPLEMENTED",
      ].includes(
        data.result
      )
    );
  }

  elements.resultCard.hidden =
    false;

  elements.resultCard
    .scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
}


// ========================================================
// Document embedding
// ========================================================

async function embedDocument() {
  const number =
    elements.watermarkNumber
      .value
      .trim();

  if (
    !selectedDocument
    || !selectedDocumentType
    || !WATERMARK_NUMBER.test(
      number
    )
  ) {
    setError(
      elements.embedError,
      "请填写有效水印号码并选择PDF、PPTX或DOCX文档"
    );

    return;
  }

  startProcessing();

  try {
    const contentType =
      getDocumentMime(
        selectedDocumentType,
        selectedDocument
      );

    const response =
      await fetch(
        "/api/embed",
        {
          method: "POST",

          headers: {
            "Content-Type":
              contentType,

            "X-File-Name":
              encodeURIComponent(
                selectedDocument.name
              ),

            "X-Watermark-Number":
              encodeURIComponent(
                number
              ),
          },

          body: selectedDocument,
        }
      );

    const data =
      await response
        .json()
        .catch(
          () => ({
            result:
              "PROCESSING_ERROR",

            message:
              "本地服务返回了无法识别的结果",
          })
        );

    stopProcessing();

    showResult(
      data,
      response.ok
    );

  } catch (error) {
    stopProcessing();

    showResult(
      {
        result:
          "PROCESSING_ERROR",

        message:
          "无法连接本地水印服务",

        description:
          "请确认服务仍在运行，然后重新提交。",
      },

      false
    );
  }
}


// ========================================================
// Photo tracing
// ========================================================

async function traceImage() {
  if (!selectedImage) {
    return;
  }

  startProcessing();

  try {
    const response =
      await fetch(
        "/api/trace",
        {
          method: "POST",

          headers: {
            "Content-Type":
              selectedImage.type
              || "application/octet-stream",

            "X-File-Name":
              encodeURIComponent(
                selectedImage.name
              ),
          },

          body: selectedImage,
        }
      );

    const data =
      await response
        .json()
        .catch(
          () => ({
            result:
              "PROCESSING_ERROR",

            message:
              "本地服务返回了无法识别的结果",
          })
        );

    stopProcessing();

    showResult(
      data,
      response.ok
    );

  } catch (error) {
    stopProcessing();

    showResult(
      {
        result:
          "PROCESSING_ERROR",

        message:
          "无法连接本地溯源服务",

        description:
          "请确认服务仍在运行，然后重新提交。",
      },

      false
    );
  }
}


// ========================================================
// Health
// ========================================================

async function checkHealth() {
  try {
    const response =
      await fetch(
        "/api/health",
        {
          cache: "no-store",
        }
      );

    const data =
      await response.json();

    if (
      !response.ok
      || !data.ok
      || !data.registry_ready
    ) {
      throw new Error();
    }

    elements.serviceState.dataset.state =
      "online";

    elements.serviceText.textContent =
      "本地服务已连接";

  } catch {
    elements.serviceState.dataset.state =
      "offline";

    elements.serviceText.textContent =
      "本地服务未连接";
  }
}


// ========================================================
// Watermark library helpers
// ========================================================

function formatIssuedAt(value) {
  if (!value) {
    return "时间未知";
  }

  const date =
    new Date(value);

  return Number.isNaN(
    date.getTime()
  )
    ? value
    : date.toLocaleString(
        "zh-CN",
        {
          hour12: false,
        }
      );
}


function makeLibraryAction(
  label,
  className,
  href
) {
  const action =
    document.createElement(
      "a"
    );

  action.className =
    className;

  action.textContent =
    label;

  action.href =
    href;

  if (label === "查看") {
    action.target =
      "_blank";

    action.rel =
      "noopener";
  }

  return action;
}


// ========================================================
// Delete watermark
// ========================================================

async function deleteWatermark(
  item,
  button
) {
  const displayNumber =
    item.watermark_number
    || "历史水印";

  if (
    !window.confirm(
      `确定删除水印“${displayNumber}”吗？\n\n`
      + "该版本会停止参与溯源，"
      + "发行文档和Manifest将移入本机回收目录。"
    )
  ) {
    return;
  }

  button.disabled = true;
  button.textContent =
    "删除中…";

  try {
    const response =
      await fetch(
        `/api/watermarks/${
          encodeURIComponent(
            item.id
          )
        }`,

        {
          method: "DELETE",

          headers: {
            "X-Confirm-Delete":
              item.id,
          },
        }
      );

    const data =
      await response.json();

    if (
      !response.ok
      || !data.deleted
    ) {
      throw new Error(
        data.error
        || data.message
        || "删除失败"
      );
    }

    await loadWatermarkLibrary();

  } catch (error) {
    window.alert(
      error.message
      || "删除失败，请稍后重试"
    );

    button.disabled =
      false;

    button.textContent =
      "删除";
  }
}


// ========================================================
// Render watermark library
// ========================================================

function renderWatermarkLibrary(
  data
) {
  elements.documentLibrary
    .replaceChildren();

  const documents =
    data.documents
    || [];

  if (!documents.length) {
    elements.libraryState.textContent =
      "当前注册库中还没有可用的水印文档。";

    elements.libraryState.hidden =
      false;

    return;
  }

  elements.libraryState.textContent =
    `${
      data.document_count
    } 份文档 · ${
      data.watermark_count
    } 个可用水印`;

  elements.libraryState.hidden =
    false;

  documents.forEach(
    (documentRecord) => {
      const card =
        document.createElement(
          "article"
        );

      card.className =
        "document-card";

      const header =
        document.createElement(
          "header"
        );

      const titleWrap =
        document.createElement(
          "div"
        );

      const title =
        document.createElement(
          "h3"
        );

      const meta =
        document.createElement(
          "p"
        );

      title.textContent =
        documentRecord.source_name;

      const unit =
        getDocumentUnit(
          documentRecord.source_type,
          documentRecord.render_unit_type
        );

      const typeLabel =
        getDocumentLabel(
          documentRecord.source_type
        );

      meta.textContent =
        `${
          typeLabel
        } · ${
          documentRecord.page_count
          || "—"
        } ${
          unit
        } · ${
          documentRecord.watermark_count
        } 个水印版本`;

      titleWrap.append(
        title,
        meta
      );

      const count =
        document.createElement(
          "span"
        );

      count.textContent =
        String(
          documentRecord.watermark_count
        );

      header.append(
        titleWrap,
        count
      );

      card.append(
        header
      );

      documentRecord.watermarks.forEach(
        (item) => {
          const row =
            document.createElement(
              "div"
            );

          row.className =
            "watermark-row";

          const info =
            document.createElement(
              "div"
            );

          const number =
            document.createElement(
              "strong"
            );

          const details =
            document.createElement(
              "small"
            );

          number.textContent =
            item.watermark_number
            || "历史水印（未设置号码）";

          details.textContent =
            `${
              formatIssuedAt(
                item.issued_at
              )
            }${
              item.file_name
                ? ` · ${item.file_name}`
                : ""
            }`;

          info.append(
            number,
            details
          );

          const actions =
            document.createElement(
              "div"
            );

          actions.className =
            "watermark-actions";

          if (
            item.output_available
          ) {
            // PDF 才有 preview_url。
            if (item.preview_url) {
              actions.append(
                makeLibraryAction(
                  "查看",
                  "row-preview",
                  item.preview_url
                )
              );
            }

            // PDF / PPTX 都应有 download_url。
            if (item.download_url) {
              actions.append(
                makeLibraryAction(
                  "下载",
                  "row-download",
                  item.download_url
                )
              );
            }

          } else {
            const missing =
              document.createElement(
                "span"
              );

            missing.className =
              "missing-file";

            missing.textContent =
              "文件缺失";

            actions.append(
              missing
            );
          }

          const remove =
            document.createElement(
              "button"
            );

          remove.type =
            "button";

          remove.className =
            "row-delete";

          remove.textContent =
            "删除";

          remove.addEventListener(
            "click",
            () => deleteWatermark(
              item,
              remove
            )
          );

          actions.append(
            remove
          );

          row.append(
            info,
            actions
          );

          card.append(
            row
          );
        }
      );

      elements.documentLibrary
        .append(card);
    }
  );
}


// ========================================================
// Load watermark library
// ========================================================

async function loadWatermarkLibrary() {
  elements.libraryState.hidden =
    false;

  elements.libraryState.textContent =
    "正在读取水印记录……";

  elements.documentLibrary
    .replaceChildren();

  elements.refreshLibrary.disabled =
    true;

  try {
    const response =
      await fetch(
        "/api/watermarks",
        {
          cache: "no-store",
        }
      );

    const data =
      await response.json();

    if (!response.ok) {
      throw new Error(
        data.error
        || "读取失败"
      );
    }

    renderWatermarkLibrary(
      data
    );

  } catch (error) {
    elements.libraryState.textContent =
      `无法读取水印记录：${
        error.message
        || "请检查本地服务"
      }`;

  } finally {
    elements.refreshLibrary.disabled =
      false;
  }
}


// ========================================================
// Drag & drop
// ========================================================

function enableDropZone(
  zone,
  select
) {
  [
    "dragenter",
    "dragover",
  ].forEach(
    (name) => {
      zone.addEventListener(
        name,
        (event) => {
          event.preventDefault();

          zone.classList.add(
            "is-dragging"
          );
        }
      );
    }
  );

  [
    "dragleave",
    "drop",
  ].forEach(
    (name) => {
      zone.addEventListener(
        name,
        (event) => {
          event.preventDefault();

          zone.classList.remove(
            "is-dragging"
          );
        }
      );
    }
  );

  zone.addEventListener(
    "drop",
    (event) => {
      select(
        event.dataTransfer
          .files[0]
      );
    }
  );
}


// ========================================================
// Event bindings
// ========================================================

elements.embedModeButton
  .addEventListener(
    "click",
    () => selectMode(
      "embed"
    )
  );


elements.traceModeButton
  .addEventListener(
    "click",
    () => selectMode(
      "trace"
    )
  );


elements.manageModeButton
  .addEventListener(
    "click",
    () => selectMode(
      "manage"
    )
  );


elements.refreshLibrary
  .addEventListener(
    "click",
    loadWatermarkLibrary
  );


elements.watermarkNumber
  .addEventListener(
    "input",
    () => {
      elements.watermarkNumber.value =
        elements.watermarkNumber
          .value
          .toUpperCase();

      setError(
        elements.embedError
      );

      updateEmbedReady();
    }
  );


// --------------------------------------------------------
// PDF / PPTX document upload
// --------------------------------------------------------

elements.pdfDropZone
  .addEventListener(
    "click",
    () => elements.pdfInput.click()
  );


elements.pdfInput
  .addEventListener(
    "change",
    () => selectDocument(
      elements.pdfInput
        .files[0]
    )
  );


elements.removePdf
  .addEventListener(
    "click",
    clearDocument
  );


elements.embedButton
  .addEventListener(
    "click",
    embedDocument
  );


enableDropZone(
  elements.pdfDropZone,
  selectDocument
);


// --------------------------------------------------------
// Trace image upload
// --------------------------------------------------------

elements.dropZone
  .addEventListener(
    "click",
    () => elements.fileInput.click()
  );


elements.fileInput
  .addEventListener(
    "change",
    () => selectImage(
      elements.fileInput
        .files[0]
    )
  );


elements.removeFile
  .addEventListener(
    "click",
    clearImage
  );


elements.traceButton
  .addEventListener(
    "click",
    traceImage
  );


enableDropZone(
  elements.dropZone,
  selectImage
);


// --------------------------------------------------------
// Copy result
// --------------------------------------------------------

elements.copyPrimary
  .addEventListener(
    "click",
    async () => {
      try {
        await navigator.clipboard
          .writeText(
            elements.primaryResultValue
              .textContent
          );

        elements.copyPrimary.textContent =
          "已复制";

        window.setTimeout(
          () => {
            elements.copyPrimary.textContent =
              "复制";
          },

          1400
        );

      } catch {
        elements.copyPrimary.textContent =
          "复制失败";
      }
    }
  );


// --------------------------------------------------------
// Retry
// --------------------------------------------------------

elements.retryButton
  .addEventListener(
    "click",
    () => {
      elements.resultCard.hidden =
        true;

      resetProcessSteps();

      document
        .querySelector(
          ".mode-tabs"
        )
        .scrollIntoView({
          behavior: "smooth",
        });
    }
  );


// ========================================================
// Initial state
// ========================================================

selectMode("embed");
checkHealth();