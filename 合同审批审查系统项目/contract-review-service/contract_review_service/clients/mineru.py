"""
【来自掌柜智库，加了封装】MinerU 文档解析客户端

掌柜智库原版：导入阶段用 MinerU 解析 PDF
本项目改造：加了三种模式适配不同环境

三种模式：
1. MockMineruClient：本地演示，读同名 .md 文件
2. MineruApiClient：云端 API（和掌柜智库一致）
3. MineruCliClient：本地部署 CLI

额外功能：
- detect_scanned()：用 PyMuPDF 判断是不是扫描件
- inject_page_markers()：给 Markdown 插 [[PAGE=n]] 页码标记
"""
from __future__ import annotations

import html
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from contract_review_service.common.config import BASE_DIR, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MineruResult:
    markdown: str
    is_scanned: bool
    page_count: int
    source: str


class MineruClient(Protocol):
    def parse_pdf(self, path: Path, *, ocr: bool) -> str: ...


def detect_scanned(path: Path) -> bool:
    """通过 PDF 文本层判断是否扫描件；图片合同一律按扫描件处理。"""
    if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        return True
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        return False

    doc = pymupdf.open(path)
    try:
        page_count = doc.page_count
        if page_count == 0:
            return True
        pages_with_text = sum(
            1
            for page in doc
            if len(page.get_text("text").strip()) >= 10
        )
    finally:
        doc.close()
    return pages_with_text / page_count < 0.5


def _strip_tags(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _block_snippet(block: dict[str, Any]) -> str:
    text = block.get("text") or block.get("table_body") or ""
    clean = _strip_tags(str(text))
    return clean[:60].strip()


def inject_page_markers(markdown: str, content_list: list[dict[str, Any]]) -> str:
    """按 MinerU content_list 的 page_idx 给 Markdown 插入 [[PAGE=n]] 标记。

    full.md 的块顺序与 content_list 基本一致，因此按顺序查找文本片段并插入
    页码标记；表格/图片块找不到时跳过，不影响正文。
    """
    if not content_list:
        return f"[[PAGE=1]]\n{markdown}"

    cursor = 0
    current_page: int | None = None
    parts: list[str] = []

    for block in content_list:
        snippet = _block_snippet(block)
        if not snippet:
            continue
        index = markdown.find(snippet, cursor)
        if index < 0:
            # 表格、公式或跨行文本可能无法精确匹配，退化为后续块继续找。
            continue
        page = int(block.get("page_idx", 0)) + 1
        if page != current_page:
            parts.append(markdown[cursor:index])
            parts.append(f"\n[[PAGE={page}]]\n")
            cursor = index
            current_page = page

    parts.append(markdown[cursor:])
    result = "".join(parts)
    if "[[PAGE=" not in result:
        result = f"[[PAGE=1]]\n{result}"
    return result


def _find_content_list(root: Path) -> Path | None:
    candidates = list(root.rglob("*content_list.json"))
    return candidates[0] if candidates else None


def _find_markdown(root: Path) -> Path | None:
    candidates = sorted(root.rglob("*.md"))
    # 优先 full.md / 与 PDF 同名的 md
    for path in candidates:
        if path.name in {"full.md", "full.txt"}:
            return path
    return candidates[0] if candidates else None


class MockMineruClient:
    """本地演示：读取与 PDF/图片同名的 .md 文件，模拟 MinerU 输出。"""

    def parse_pdf(self, path: Path, *, ocr: bool) -> str:
        # 存储后的文件名可能带 "0_" 前缀，因此同时按 samples 目录同名文件回退。
        clean_stem = re.sub(r"^\d+_", "", path.stem)
        candidates = [
            path.with_suffix(".md"),
            Path(get_settings().sample_dir) / f"{clean_stem}.md",
            BASE_DIR / "samples" / f"{clean_stem}.md",
            path.with_name(f"{clean_stem}.md"),
        ]
        sibling = next((item for item in candidates if item.exists()), None)
        if sibling is None:
            raise FileNotFoundError(
                f"MinerU mock 未找到同名 Markdown：{path.with_suffix('.md')}。"
                "请配置 CONTRACT_MINERU_MODE=api/cli，或在 samples 放同名 .md。"
            )
        text = sibling.read_text(encoding="utf-8")
        return f"[[PAGE=1]]\n{text}"


def _run_curl(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    if shutil.which("curl") is None:
        raise RuntimeError("当前环境没有 curl；请安装 curl 或修复 Python 的 SSL 访问")
    return subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _curl_json(args: list[str], timeout: int) -> dict:
    completed = _run_curl(args, timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"curl 访问 MinerU 失败：{completed.stderr[-300:]}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MinerU 返回非 JSON：{completed.stdout[:200]}") from exc


class MineruApiClient:
    """MinerU 云端 API（v4），默认 httpx，SSL 不兼容时自动回退 curl。"""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.mineru_token:
            raise RuntimeError("未配置 CONTRACT_MINERU_TOKEN，无法调用 MinerU 云端 API")
        self.token = settings.mineru_token
        self.base_url = settings.mineru_base_url.rstrip("/")
        self.model_version = settings.mineru_model_version
        self.poll_interval = settings.mineru_poll_interval_seconds
        self.timeout = settings.mineru_timeout_seconds
        self.transport = settings.mineru_transport.lower()
        self._force_curl = self.transport == "curl"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        if not self._force_curl:
            try:
                if method == "POST":
                    response = httpx.post(
                        url,
                        headers=self._headers(),
                        json=payload,
                        timeout=60,
                    )
                else:
                    response = httpx.get(url, headers=self._headers(), timeout=60)
                response.raise_for_status()
                return response.json()
            except Exception:
                if self.transport == "httpx":
                    raise
                logger.warning("httpx 访问 MinerU 失败，自动改用 curl")
                self._force_curl = True

        args = [
            "-X",
            method,
            url,
            "-H",
            f"Authorization: Bearer {self.token}",
            "-H",
            "Content-Type: application/json",
        ]
        if payload is not None:
            args += ["-d", json.dumps(payload, ensure_ascii=False)]
        return _curl_json(args, 60)

    def _put_file(self, url: str, path: Path) -> None:
        if not self._force_curl:
            try:
                with path.open("rb") as file:
                    response = httpx.put(url, content=file.read(), timeout=120)
                response.raise_for_status()
                return
            except Exception:
                if self.transport == "httpx":
                    raise
                logger.warning("httpx 上传附件失败，自动改用 curl")
                self._force_curl = True

        completed = _run_curl(
            ["-X", "PUT", "--data-binary", f"@{path}", url],
            120,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"curl 上传 MinerU 附件失败：{completed.stderr[-300:]}")

    def _download(self, url: str, target: Path) -> None:
        if not self._force_curl:
            try:
                with httpx.stream("GET", url, timeout=120) as response:
                    response.raise_for_status()
                    with target.open("wb") as file:
                        for chunk in response.iter_bytes():
                            file.write(chunk)
                return
            except Exception:
                if self.transport == "httpx":
                    raise
                logger.warning("httpx 下载 MinerU 结果失败，自动改用 curl")
                self._force_curl = True

        completed = _run_curl(["-L", "-o", str(target), url], 120)
        if completed.returncode != 0:
            raise RuntimeError(f"curl 下载 MinerU 结果失败：{completed.stderr[-300:]}")

    def _upload(self, path: Path) -> str:
        payload = self._request_json(
            "POST",
            "/file-urls/batch",
            {
                "files": [{"name": path.name, "data_id": "contract-review"}],
                "model_version": self.model_version,
            },
        )
        if payload.get("code") != 0:
            raise RuntimeError(f"MinerU 申请上传地址失败：{payload}")

        data = payload["data"]
        batch_id = data["batch_id"]
        upload_url = data["file_urls"][0]
        self._put_file(upload_url, path)
        return batch_id

    def _wait_for_zip(self, batch_id: str) -> str:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            payload = self._request_json(
                "GET",
                f"/extract-results/batch/{batch_id}",
            )
            if payload.get("code") != 0:
                raise RuntimeError(f"MinerU 查询结果失败：{payload}")
            result = payload["data"]["extract_result"][0]
            state = result.get("state")
            if state == "done":
                return result["full_zip_url"]
            if state in {"failed", "error"}:
                raise RuntimeError(f"MinerU 解析失败：{result}")
            logger.info("MinerU 解析中：%s", state)
            time.sleep(self.poll_interval)
        raise TimeoutError("MinerU 解析超时")

    def parse_pdf(self, path: Path, *, ocr: bool) -> str:
        batch_id = self._upload(path)
        zip_url = self._wait_for_zip(batch_id)
        with tempfile.TemporaryDirectory(prefix="mineru_") as tmp:
            root = Path(tmp)
            zip_path = root / "result.zip"
            self._download(zip_url, zip_path)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(root)
            markdown_path = _find_markdown(root)
            if markdown_path is None:
                raise RuntimeError("MinerU 返回结果中没有 Markdown")
            markdown = markdown_path.read_text(encoding="utf-8")
            content_path = _find_content_list(root)
            content_list = (
                json.loads(content_path.read_text(encoding="utf-8"))
                if content_path
                else []
            )
            return inject_page_markers(markdown, content_list)


class MineruCliClient:
    """本地部署 MinerU：调用 mineru 命令，扫描件走 OCR 方法。"""

    def __init__(self) -> None:
        settings = get_settings()
        self.command = settings.mineru_cli_command
        self.timeout = settings.mineru_timeout_seconds

    def parse_pdf(self, path: Path, *, ocr: bool) -> str:
        with tempfile.TemporaryDirectory(prefix="mineru_") as tmp:
            output_dir = Path(tmp)
            command = [
                self.command,
                "-p",
                str(path),
                "-o",
                str(output_dir),
                "-m",
                "ocr" if ocr else "auto",
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"MinerU CLI 执行失败：{completed.stderr[-500:]}"
                )
            markdown_path = _find_markdown(output_dir)
            if markdown_path is None:
                raise RuntimeError("MinerU CLI 输出中没有 Markdown")
            markdown = markdown_path.read_text(encoding="utf-8")
            content_path = _find_content_list(output_dir)
            content_list = (
                json.loads(content_path.read_text(encoding="utf-8"))
                if content_path
                else []
            )
            return inject_page_markers(markdown, content_list)


def get_mineru_client() -> MineruClient:
    mode = get_settings().mineru_mode.lower()
    if mode == "api":
        return MineruApiClient()
    if mode == "cli":
        return MineruCliClient()
    return MockMineruClient()
