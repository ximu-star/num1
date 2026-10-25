"""把手册 PDF 切片、向量化并写入 Milvus。

用法（在 contract-review-service 目录下，用项目 venv）：
    ..\\ai-service\\.venv\\Scripts\\python.exe scripts\\import_manual.py --reset
    ..\\ai-service\\.venv\\Scripts\\python.exe scripts\\import_manual.py --pdf samples\\xxx.pdf

依赖：pymupdf、sentence-transformers、pymilvus（已在项目 venv 中）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf

# 允许从项目根目录或 scripts 目录运行
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract_review_service.services.semantic import index_chunks  # noqa: E402

DEFAULT_PDF = ROOT / "samples" / "工程项目造价规范操作手册.pdf"
CHUNK_SIZE = 300
OVERLAP = 30
MIN_CHUNK = 20


def _split_page(text: str, page_no: int, source: str, start_index: int) -> list[dict]:
    """按段落聚合到约 300 字，超长段落再按 300/30 切分。"""
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    chunks: list[dict] = []
    buffer = ""

    def flush(value: str) -> None:
        clean = value.strip()
        if len(clean) < MIN_CHUNK:
            return
        chunks.append(
            {
                "text": clean,
                "page": page_no,
                "source": source,
                "chunk_index": start_index + len(chunks),
            }
        )

    for paragraph in paragraphs:
        if len(paragraph) > CHUNK_SIZE:
            if buffer:
                flush(buffer)
                buffer = ""
            start = 0
            while start < len(paragraph):
                flush(paragraph[start:start + CHUNK_SIZE])
                if start + CHUNK_SIZE >= len(paragraph):
                    break
                start += CHUNK_SIZE - OVERLAP
            continue

        candidate = f"{buffer}\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= CHUNK_SIZE:
            buffer = candidate
        else:
            flush(buffer)
            buffer = paragraph

    if buffer:
        flush(buffer)
    return chunks


def load_pdf_chunks(pdf_path: Path) -> list[dict]:
    doc = pymupdf.open(pdf_path)
    chunks: list[dict] = []
    try:
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text")
            chunks.extend(
                _split_page(text, page_no, pdf_path.name, len(chunks))
            )
    finally:
        doc.close()
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="导入手册切片到 Milvus")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="手册 PDF 路径")
    parser.add_argument("--reset", action="store_true", help="先删除并重建集合")
    args = parser.parse_args()

    if not args.pdf.exists():
        raise SystemExit(f"找不到手册 PDF：{args.pdf}")

    chunks = load_pdf_chunks(args.pdf)
    print(f"手册：{args.pdf.name}，切片数：{len(chunks)}")
    if not chunks:
        raise SystemExit("没有切出有效内容，请检查 PDF 是否为扫描件")

    count = index_chunks(chunks, reset=args.reset)
    print(f"已写入 Milvus：{count} 条")


if __name__ == "__main__":
    main()
