"""Dependency-free local web demo for PDF watermark issuing and tracing.

The browser uploads a raw JPG/PNG body to ``POST /api/trace``.  This keeps the
demo independent from Flask and multipart parsing packages while preserving a
small, auditable local attack surface.
"""

from __future__ import annotations

import argparse
import json
import math
import secrets
import shutil
import sys
import threading
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np


WEB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from document_registry import (  # noqa: E402
    load_document_registry,
    normalize_trace_token,
    normalize_watermark_number,
    retire_document_issue,
)
from pdf_pipeline import (  # noqa: E402
    DEFAULT_KEY,
    embed_document_pdf,
    trace_document_photo,
)


ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
}
STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


@dataclass
class DemoConfig:
    registry_path: Path
    upload_dir: Path
    report_dir: Path
    pdf_input_dir: Path
    pdf_output_dir: Path
    document_assets_dir: Path
    trash_dir: Path
    key: str = DEFAULT_KEY
    max_upload_bytes: int = 30 * 1024 * 1024
    max_pdf_upload_bytes: int = 100 * 1024 * 1024
    max_pixels: int = 40_000_000
    keep_uploads: bool = False
    debug: bool = False
    trace_lock: threading.Lock = field(default_factory=threading.Lock)


class UploadError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def _finite_round(value, digits=3):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, int(digits))


def _failure_presentation(status):
    status = str(status or "TRACE_NOT_CONFIRMED")
    if "PAGE_ALIGNMENT" in status:
        return (
            "未识别到有效文档页面",
            "请让照片包含更多文字、标题或图表内容，并保证画面清晰。",
            ["保留更大的文档区域", "避免只拍摄空白页边缘", "减小拍摄角度后重试"],
        )
    if "INSUFFICIENT_OBSERVED_BITS" in status:
        return (
            "可见水印信息不足",
            "当前拍摄区域过小，暂时无法可靠确认水印来源。",
            ["扩大拍摄范围", "尽量保留页面20%以上内容", "避免裁剪和二次压缩"],
        )
    if "LOW_PILOT" in status:
        return (
            "页面同步信息不足",
            "拍摄角度、透视或局部裁剪使页面同步不稳定。",
            ["减小拍摄角度", "保持手机稳定并重新对焦", "增加可见页面范围"],
        )
    if "LOW_MARGIN" in status:
        return (
            "无法可靠区分水印来源",
            "检测到候选水印，但当前证据不足以给出可靠溯源结论。",
            ["上传手机原始照片", "避免微信压缩图片", "重新拍摄更清晰的页面区域"],
        )
    if "LOW_Z_SCORE" in status or "LOW_HARD_MATCH_RATE" in status:
        return (
            "水印信号强度不足",
            "摩尔纹、失焦、反光或局部几何失真影响了水印提取。",
            ["重新点击对焦", "轻微改变拍摄距离", "避免反光并使用原始JPG"],
        )
    return (
        "暂未确认水印来源",
        "系统没有获得足够可靠的水印证据。",
        ["保留更大的页面范围", "确保文字清晰", "使用手机原始照片重试"],
    )


def build_public_response(report, request_id):
    """Create a UI-safe response; rejected candidates never expose a token."""

    accepted = bool(report.get("accepted"))
    status = str(report.get("status") or "UNKNOWN")
    extraction = report.get("extraction") or {}
    details = extraction.get("details") or {}
    synchronization = report.get("synchronization") or {}
    pilot = synchronization.get("best") or {}
    local_sync = synchronization.get("local_partition_sync") or {}
    decision = report.get("registry_decision") or {}
    selected = decision.get("selected") or {}
    coverage = details.get("valid_dct_unit_ratio")
    if coverage is None:
        coverage = (details.get("local_partition_selection") or {}).get(
            "global_valid_dct_unit_ratio"
        )
    technical = {
        "decision_status": status,
        "page_coverage": _finite_round(coverage, 4),
        "observed_bits": extraction.get("observed_bits"),
        "erasures": extraction.get("erasures"),
        "crc_pass": bool(extraction.get("crc_pass")),
        "pilot_status": synchronization.get("status"),
        "pilot_observed_bits": pilot.get("observed_bits"),
        "pilot_correlation": _finite_round(
            pilot.get("normalized_correlation"), 4
        ),
        "z_score": _finite_round(selected.get("z_score"), 4),
        "normalized_score": _finite_round(
            selected.get("normalized_score"), 4
        ),
        "hard_match_rate": _finite_round(
            selected.get("hard_match_rate"), 4
        ),
        "margin_z": _finite_round(selected.get("margin_z"), 4),
        "alignment_method": (report.get("alignment") or {}).get("method"),
        "alignment_inliers": (report.get("alignment") or {}).get("inliers"),
        "local_sync_status": local_sync.get("status"),
        "score_source": local_sync.get("selected_score_source") or "global",
        "elapsed_ms": _finite_round(report.get("elapsed_ms"), 1),
    }
    response = {
        "request_id": request_id,
        "accepted": accepted,
        "result": "TRACE_SUCCESS" if accepted else "TRACE_NOT_CONFIRMED",
        "technical": technical,
    }
    if accepted:
        issue = report.get("issue") or selected.get("issue") or {}
        response.update({
            "message": "水印溯源成功",
            "description": "已在注册库中确认该文档的水印来源。",
            "watermark_number": issue.get("watermark_number"),
            "document_id": report.get("document_id"),
            "page_index": report.get("page_index"),
            "trace_id": report.get("trace_id"),
            "trace_token": report.get("trace_token"),
        })
    else:
        title, description, suggestions = _failure_presentation(status)
        response.update({
            "message": title,
            "description": description,
            "reason": status,
            "suggestions": suggestions,
        })
    return response


def build_watermark_catalog(registry):
    """Return active issue records grouped by source document for the UI."""

    groups = {}
    deleted_count = 0
    for token, issue in registry.get("issues", {}).items():
        if issue.get("status", "issued") != "issued":
            deleted_count += 1
            continue
        document_id = issue.get("document_id")
        document = registry.get("documents", {}).get(document_id, {})
        output_path = Path(issue.get("output_pdf", "")) if issue.get("output_pdf") else None
        artifact_ready = bool(output_path and output_path.is_file())
        group = groups.setdefault(document_id, {
            "document_id": document_id,
            "source_name": document.get("source_name") or "未命名文档.pdf",
            "page_count": document.get("page_count"),
            "watermarks": [],
        })
        group["watermarks"].append({
            "id": token,
            "watermark_number": issue.get("watermark_number"),
            "issued_at": issue.get("issued_at"),
            "file_name": output_path.name if output_path else None,
            "output_available": artifact_ready,
            "preview_url": f"/api/watermarks/{token}/preview" if artifact_ready else None,
            "download_url": f"/api/watermarks/{token}/download" if artifact_ready else None,
        })
    documents = list(groups.values())
    for document in documents:
        document["watermarks"].sort(
            key=lambda item: item.get("issued_at") or "", reverse=True
        )
        document["watermark_count"] = len(document["watermarks"])
    documents.sort(
        key=lambda item: (
            item["watermarks"][0].get("issued_at") if item["watermarks"] else ""
        ),
        reverse=True,
    )
    return {
        "documents": documents,
        "document_count": len(documents),
        "watermark_count": sum(item["watermark_count"] for item in documents),
        "deleted_count": deleted_count,
    }


def make_request_handler(config):
    class TraceDemoHandler(BaseHTTPRequestHandler):
        server_version = "DocumentTraceDemo/2.0A.2"

        def _common_headers(self, content_type, content_length, cache=False):
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(int(content_length)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' blob: data:; "
                "style-src 'self'; script-src 'self'; connect-src 'self'",
            )
            self.send_header(
                "Cache-Control",
                "public, max-age=300" if cache else "no-store",
            )

        def _send_json(self, status, payload, request_id=None):
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(int(status))
            self._common_headers("application/json; charset=utf-8", len(body))
            if request_id:
                self.send_header("X-Request-ID", request_id)
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, file_name, content_type):
            path = WEB_ROOT / file_name
            if not path.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "资源不存在"})
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._common_headers(content_type, len(body), cache=True)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urllib.parse.urlparse(self.path).path
            if route == "/api/health":
                self._send_json(HTTPStatus.OK, {
                    "ok": True,
                    "service": "document-watermark-trace",
                    "version": "V2.0A.2",
                    "registry_ready": config.registry_path.is_file(),
                })
                return
            if route == "/api/watermarks":
                with config.trace_lock:
                    catalog = build_watermark_catalog(
                        load_document_registry(config.registry_path)
                    )
                self._send_json(HTTPStatus.OK, catalog)
                return
            route_parts = [part for part in route.split("/") if part]
            if (
                len(route_parts) == 4
                and route_parts[:2] == ["api", "watermarks"]
                and route_parts[3] in {"preview", "download"}
            ):
                self._send_registered_pdf(
                    route_parts[2], download=route_parts[3] == "download"
                )
                return
            if route.startswith("/api/download/"):
                self._send_pdf_download(route.removeprefix("/api/download/"))
                return
            static = STATIC_ROUTES.get(route)
            if static:
                self._send_static(*static)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "页面不存在"})

        def _send_registered_pdf(self, encoded_token, download=False):
            try:
                token = normalize_trace_token(urllib.parse.unquote(encoded_token))
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "水印记录编号无效"})
                return
            with config.trace_lock:
                registry = load_document_registry(config.registry_path)
                issue = registry.get("issues", {}).get(token)
                if issue is None or issue.get("status", "issued") != "issued":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "水印记录不存在"})
                    return
                path = Path(issue.get("output_pdf", ""))
                if not path.is_file() or path.suffix.lower() != ".pdf":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "水印PDF文件不存在"})
                    return
                body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._common_headers("application/pdf", len(body))
            quoted = urllib.parse.quote(path.name)
            disposition = "attachment" if download else "inline"
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{quoted}",
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_pdf_download(self, encoded_name):
            file_name = urllib.parse.unquote(encoded_name)
            if (
                not file_name
                or Path(file_name).name != file_name
                or Path(file_name).suffix.lower() != ".pdf"
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "下载文件名无效"})
                return
            path = config.pdf_output_dir / file_name
            if not path.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "水印PDF不存在"})
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._common_headers("application/pdf", len(body))
            quoted = urllib.parse.quote(file_name)
            self.send_header(
                "Content-Disposition", f"attachment; filename*=UTF-8''{quoted}"
            )
            self.end_headers()
            self.wfile.write(body)

        def _receive_image(self, request_id):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise UploadError("文件长度无效") from error
            if length <= 0:
                raise UploadError("没有收到图片文件")
            if length > int(config.max_upload_bytes):
                raise UploadError(
                    "图片超过上传大小限制", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                )
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
            extension = ALLOWED_CONTENT_TYPES.get(content_type)
            if extension is None:
                raise UploadError("仅支持JPG、JPEG和PNG格式")
            body = self.rfile.read(length)
            if len(body) != length:
                raise UploadError("图片上传不完整")
            array = np.frombuffer(body, dtype=np.uint8)
            decoded = cv2.imdecode(array, cv2.IMREAD_COLOR)
            if decoded is None:
                raise UploadError("文件不是有效的JPG或PNG图片")
            height, width = decoded.shape[:2]
            if width < 96 or height < 96:
                raise UploadError("图片尺寸过小，请上传更清晰的原始照片")
            if width * height > int(config.max_pixels):
                raise UploadError("图片像素尺寸过大，请先等比例缩小")
            config.upload_dir.mkdir(parents=True, exist_ok=True)
            upload_path = config.upload_dir / f"{request_id}{extension}"
            upload_path.write_bytes(body)
            return upload_path, {"width": width, "height": height, "bytes": length}

        def _receive_pdf(self, request_id):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise UploadError("文件长度无效") from error
            if length <= 0:
                raise UploadError("没有收到PDF文件")
            if length > int(config.max_pdf_upload_bytes):
                raise UploadError(
                    "PDF超过上传大小限制", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                )
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type != "application/pdf":
                raise UploadError("仅支持PDF文件")
            body = self.rfile.read(length)
            if len(body) != length:
                raise UploadError("PDF上传不完整")
            if not body.lstrip().startswith(b"%PDF-"):
                raise UploadError("文件不是有效的PDF")
            encoded_name = self.headers.get("X-File-Name", "document.pdf")
            source_name = urllib.parse.unquote(encoded_name)
            safe_stem = "".join(
                char if char.isalnum() or char in "-_" else "_"
                for char in Path(source_name).stem
            ).strip("_")[:80] or "document"
            config.pdf_input_dir.mkdir(parents=True, exist_ok=True)
            input_path = config.pdf_input_dir / f"{safe_stem}_{request_id}.pdf"
            input_path.write_bytes(body)
            return input_path, source_name, length

        def _handle_trace(self, request_id):
            upload_path = None
            started = time.perf_counter()
            try:
                if not config.registry_path.is_file():
                    raise RuntimeError("文档注册库不存在")
                upload_path, image_info = self._receive_image(request_id)
                output_dir = config.report_dir / request_id
                with config.trace_lock:
                    report = trace_document_photo(
                        upload_path,
                        config.registry_path,
                        key=config.key,
                        output_dir=output_dir,
                        enable_document_rerank=True,
                        enable_local_partition_sync=True,
                    )
                public = build_public_response(report, request_id)
                public["image"] = image_info
                public["server_elapsed_ms"] = round(
                    (time.perf_counter() - started) * 1000.0, 1
                )
                self._send_json(HTTPStatus.OK, public, request_id=request_id)
            except UploadError as error:
                self._send_json(error.status, {
                    "request_id": request_id,
                    "accepted": False,
                    "result": "INVALID_UPLOAD",
                    "message": str(error),
                }, request_id=request_id)
            except Exception as error:
                traceback.print_exc()
                payload = {
                    "request_id": request_id,
                    "accepted": False,
                    "result": "PROCESSING_ERROR",
                    "message": "图片处理失败，请检查文件后重试",
                }
                if config.debug:
                    payload["debug"] = f"{type(error).__name__}: {error}"
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, payload, request_id=request_id
                )
            finally:
                if upload_path is not None and not config.keep_uploads:
                    try:
                        upload_path.unlink(missing_ok=True)
                    except OSError:
                        pass

        def _handle_embed(self, request_id):
            started = time.perf_counter()
            input_path = None
            try:
                encoded_number = self.headers.get("X-Watermark-Number", "")
                watermark_number = normalize_watermark_number(
                    urllib.parse.unquote(encoded_number)
                )
                input_path, source_name, byte_count = self._receive_pdf(request_id)
                config.pdf_output_dir.mkdir(parents=True, exist_ok=True)
                safe_stem = "".join(
                    char if char.isalnum() or char in "-_" else "_"
                    for char in Path(source_name).stem
                ).strip("_")[:80] or "document"
                output_path = config.pdf_output_dir / (
                    f"{safe_stem}_wm_{watermark_number}.pdf"
                )
                if output_path.exists():
                    raise UploadError("该水印号码的输出PDF已存在", HTTPStatus.CONFLICT)
                with config.trace_lock:
                    manifest_path, manifest = embed_document_pdf(
                        input_path,
                        config.registry_path,
                        key=config.key,
                        output_pdf=output_path,
                        assets_root=config.document_assets_dir,
                        dpi=96,
                        alpha=72.0,
                        repeat=24,
                        pilot_bits=64,
                        pilot_repeat=8,
                        pilot_alpha=90.0,
                        recipient="web_demo_user",
                        session=f"web_embed_{request_id}",
                        notes=f"用户水印号码: {watermark_number}",
                        watermark_number=watermark_number,
                        source_name=source_name,
                    )
                payload = {
                    "request_id": request_id,
                    "accepted": True,
                    "success": True,
                    "result": "EMBED_SUCCESS",
                    "message": "水印嵌入成功",
                    "description": "水印PDF已生成并登记，可立即下载。",
                    "watermark_number": watermark_number,
                    "file_name": output_path.name,
                    "download_url": "/api/download/" + urllib.parse.quote(output_path.name),
                    "preview_url": f"/api/watermarks/{manifest.get('trace_token')}/preview",
                    "page_count": manifest.get("page_count"),
                    "source_bytes": byte_count,
                    "document_id": manifest.get("document_id"),
                    "trace_id": manifest.get("trace_id"),
                    "trace_token": manifest.get("trace_token"),
                    "manifest_name": manifest_path.name,
                    "server_elapsed_ms": round(
                        (time.perf_counter() - started) * 1000.0, 1
                    ),
                    "technical": {
                        "dpi": manifest.get("dpi"),
                        "block_size": (manifest.get("watermark") or {}).get("block_size"),
                        "payload_repeat": (manifest.get("watermark") or {}).get("repeat"),
                        "payload_alpha": (manifest.get("watermark") or {}).get("alpha"),
                        "manifest": manifest_path.name,
                    },
                }
                self._send_json(HTTPStatus.OK, payload, request_id=request_id)
            except UploadError as error:
                self._send_json(error.status, {
                    "request_id": request_id,
                    "accepted": False,
                    "result": "INVALID_UPLOAD",
                    "message": str(error),
                }, request_id=request_id)
            except ValueError as error:
                message = str(error)
                status = HTTPStatus.CONFLICT if "已存在" in message else HTTPStatus.BAD_REQUEST
                self._send_json(status, {
                    "request_id": request_id,
                    "accepted": False,
                    "result": "EMBED_REJECTED",
                    "message": message,
                }, request_id=request_id)
            except Exception as error:
                traceback.print_exc()
                payload = {
                    "request_id": request_id,
                    "accepted": False,
                    "result": "PROCESSING_ERROR",
                    "message": "PDF水印嵌入失败，请检查文件后重试",
                }
                if config.debug:
                    payload["debug"] = f"{type(error).__name__}: {error}"
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, payload, request_id=request_id
                )

        def _handle_delete_watermark(self, encoded_token):
            try:
                token = normalize_trace_token(urllib.parse.unquote(encoded_token))
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "水印记录编号无效"})
                return
            if self.headers.get("X-Confirm-Delete") != token:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "缺少删除确认"})
                return
            with config.trace_lock:
                registry = load_document_registry(config.registry_path)
                issue = registry.get("issues", {}).get(token)
                if issue is None or issue.get("status", "issued") != "issued":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "水印记录不存在或已删除"})
                    return
                document = registry.get("documents", {}).get(issue.get("document_id"), {})
                trash_path = config.trash_dir / (
                    datetime.now().strftime("%Y%m%d_%H%M%S_") + token
                )
                candidates = []
                output_path = Path(issue.get("output_pdf", "")) if issue.get("output_pdf") else None
                manifest_path = Path(issue.get("manifest_path", "")) if issue.get("manifest_path") else None
                if output_path and output_path.is_file() and output_path.suffix.lower() == ".pdf":
                    candidates.append((output_path, "watermarked.pdf"))
                if manifest_path and manifest_path.is_file() and manifest_path.suffix.lower() == ".json":
                    candidates.append((manifest_path, "manifest.json"))
                assets_dir = Path(document.get("assets_dir", "")) if document.get("assets_dir") else None
                issue_dir = assets_dir / "issues" / token if assets_dir else None
                if issue_dir and issue_dir.is_dir() and issue_dir.name == token:
                    candidates.append((issue_dir, "watermarked_pages"))
                moved = []
                try:
                    trash_path.mkdir(parents=True, exist_ok=False)
                    for source, target_name in candidates:
                        target = trash_path / target_name
                        shutil.move(str(source), str(target))
                        moved.append((source, target))
                    retired = retire_document_issue(
                        config.registry_path, token, trash_path=trash_path
                    )
                except Exception:
                    for source, target in reversed(moved):
                        if target.exists() and not source.exists():
                            shutil.move(str(target), str(source))
                    try:
                        trash_path.rmdir()
                    except OSError:
                        pass
                    raise
            self._send_json(HTTPStatus.OK, {
                "deleted": True,
                "watermark_number": retired.get("watermark_number"),
                "moved_artifacts": len(moved),
                "recoverable": True,
                "message": "水印版本已删除，并移入本机回收目录",
            })

        def do_POST(self):
            route = urllib.parse.urlparse(self.path).path
            if route not in {"/api/trace", "/api/embed"}:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
                return
            request_id = (
                datetime.now().strftime("%Y%m%d_%H%M%S_")
                + secrets.token_hex(3)
            )
            if route == "/api/embed":
                self._handle_embed(request_id)
            else:
                self._handle_trace(request_id)

        def do_DELETE(self):
            route = urllib.parse.urlparse(self.path).path
            parts = [part for part in route.split("/") if part]
            if len(parts) == 3 and parts[:2] == ["api", "watermarks"]:
                self._handle_delete_watermark(parts[2])
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})

        def log_message(self, format_string, *args):
            sys.stderr.write(
                f"[{self.log_date_time_string()}] "
                + format_string % args
                + "\n"
            )

    return TraceDemoHandler


def build_parser():
    parser = argparse.ArgumentParser(description="文档水印发行与溯源本地网页Demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--registry", default=str(PROJECT_ROOT / "document_registry.json"))
    parser.add_argument("--key", default=DEFAULT_KEY)
    parser.add_argument("--upload-dir", default=str(WEB_ROOT / "runtime" / "uploads"))
    parser.add_argument("--report-dir", default=str(WEB_ROOT / "runtime" / "reports"))
    parser.add_argument("--pdf-input-dir", default=str(WEB_ROOT / "runtime" / "pdf_inputs"))
    parser.add_argument("--pdf-output-dir", default=str(WEB_ROOT / "runtime" / "outputs"))
    parser.add_argument("--document-assets-dir", default=str(WEB_ROOT / "runtime" / "document_assets"))
    parser.add_argument("--trash-dir", default=str(WEB_ROOT / "runtime" / "trash"))
    parser.add_argument("--max-upload-mb", type=int, default=30)
    parser.add_argument("--max-pdf-mb", type=int, default=100)
    parser.add_argument("--keep-uploads", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()
    config = DemoConfig(
        registry_path=Path(args.registry).resolve(),
        upload_dir=Path(args.upload_dir).resolve(),
        report_dir=Path(args.report_dir).resolve(),
        pdf_input_dir=Path(args.pdf_input_dir).resolve(),
        pdf_output_dir=Path(args.pdf_output_dir).resolve(),
        document_assets_dir=Path(args.document_assets_dir).resolve(),
        trash_dir=Path(args.trash_dir).resolve(),
        key=args.key,
        max_upload_bytes=max(1, int(args.max_upload_mb)) * 1024 * 1024,
        max_pdf_upload_bytes=max(1, int(args.max_pdf_mb)) * 1024 * 1024,
        keep_uploads=bool(args.keep_uploads),
        debug=bool(args.debug),
    )
    if not config.registry_path.is_file():
        raise FileNotFoundError(f"文档注册库不存在: {config.registry_path}")
    server = ThreadingHTTPServer(
        (args.host, int(args.port)), make_request_handler(config)
    )
    server.daemon_threads = True
    print(f"文档水印发行与溯源Demo已启动: http://{args.host}:{args.port}")
    print(f"注册库: {config.registry_path}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n正在停止服务……")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
