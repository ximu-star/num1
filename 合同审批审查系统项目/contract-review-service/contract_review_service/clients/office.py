from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from contract_review_service.common.config import get_settings

logger = logging.getLogger(__name__)

WORD_SUFFIXES = {".doc", ".docx"}


def is_word_file(path: Path) -> bool:
    return path.suffix.lower() in WORD_SUFFIXES


def _find_soffice() -> str | None:
    """查找 LibreOffice；服务器优先用 CLI，Windows 本地再用 Word COM 兜底。"""
    override = get_settings().soffice_path.strip()
    if override and Path(override).exists():
        return override

    for command in ("soffice", "libreoffice"):
        found = shutil.which(command)
        if found:
            return found

    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
    ]
    return next((path for path in candidates if Path(path).exists()), None)


def _convert_with_libreoffice(source: Path, output_dir: Path, timeout: int = 120) -> Path | None:
    soffice = _find_soffice()
    if not soffice:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            soffice,
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        logger.warning("LibreOffice 转 PDF 失败：%s", completed.stderr[-500:])
        return None

    target = output_dir / f"{source.stem}.pdf"
    return target if target.exists() else None


def _convert_with_word_com(source: Path, output_dir: Path) -> Path | None:
    """Windows 本地兜底：用 Microsoft Word COM 转 PDF。"""
    if sys.platform != "win32":
        return None

    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        logger.info("未安装 pywin32，跳过 Word COM 转换")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{source.stem}.pdf"
    word = None
    document = None

    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(
            str(source.resolve()),
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        # wdFormatPDF = 17
        document.SaveAs(str(target.resolve()), FileFormat=17)
        document.Close(False)
        document = None
        return target if target.exists() else None
    except Exception:
        logger.exception("Word COM 转 PDF 失败")
        return None
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def convert_word_to_pdf(source: Path, output_dir: Path) -> Path:
    """Word → PDF：LibreOffice 优先，Windows 本地用 Word COM 兜底。

    转换后的 PDF 继续交给 MinerU 解析；扫描件由 MinerU 走 OCR。
    """
    source = Path(source)
    output_dir = Path(output_dir)
    if not source.exists():
        raise FileNotFoundError(f"Word 文件不存在：{source}")

    pdf = _convert_with_libreoffice(source, output_dir)
    if pdf is not None:
        return pdf

    pdf = _convert_with_word_com(source, output_dir)
    if pdf is not None:
        return pdf

    raise RuntimeError(
        "Word 转 PDF 失败：请安装 LibreOffice（服务器推荐），"
        "或在 Windows 本地安装 Microsoft Word + pywin32。"
    )
