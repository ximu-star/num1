"""
【部分来自掌柜智库】合同解析模块

来自掌柜智库的部分：
- _split_long()：长文本切片（按字符切，保留 overlap）

全新写的部分：
- extract_text()：PDF 走 MinerU，Word 走 python-docx
- split_clauses()：按"第X条"切分合同条款
- extract_basic_info()：提取合同基本信息（金额/甲方/乙方/日期）
- extract_normalized()：从条款里提取结构化数值（付款天数/比例/税率）
- parse_contract()：总入口，输出 full_text + basic_info + clauses + chunks
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

CLAUSE_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?(第[一二三四五六七八九十百千零〇\d]+条[^\n]*)"
)
PAGE_PATTERN = re.compile(r"\[\[PAGE=(\d+)\]\]")

CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("payment", ("付款", "支付", "账期", "预付款", "结算")),
    ("delivery", ("交付", "工期", "进场", "供货", "交货")),
    ("acceptance", ("验收", "质量", "标准")),
    ("breach", ("违约", "赔偿", "扣罚", "罚款")),
    ("confidentiality", ("保密", "数据", "信息安全")),
    ("ip", ("知识产权", "专利", "著作权")),
    ("dispute", ("争议", "管辖", "仲裁", "诉讼")),
]


def extract_text(path: Path) -> tuple[str, bool]:
    """提取文本并返回 (文本, 是否扫描件)。

    PDF / 图片统一走 MinerU：扫描件自动 OCR，返回带 [[PAGE=n]] 标记的 Markdown。
    """
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8"), False

    if suffix in {".pdf", ".png", ".jpg", ".jpeg"}:
        from contract_review_service.clients.mineru import (
            detect_scanned,
            get_mineru_client,
        )

        is_scanned = detect_scanned(path)
        markdown = get_mineru_client().parse_pdf(path, ocr=is_scanned)
        if not markdown.strip():
            raise RuntimeError("MinerU 解析内容为空")
        return markdown, is_scanned

    if suffix in {".doc", ".docx"}:
        # Word 先转 PDF，再统一交给 MinerU（扫描件由 MinerU 走 OCR）。
        from contract_review_service.clients.office import convert_word_to_pdf

        with tempfile.TemporaryDirectory(prefix="word_to_pdf_") as tmp:
            pdf_path = convert_word_to_pdf(path, Path(tmp))
            return extract_text(pdf_path)

    raise ValueError(f"不支持的文件类型: {suffix}")


def _first_number(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except (TypeError, ValueError):
                continue
    return None


def extract_normalized(text: str) -> dict[str, float]:
    """从条款原文抽取规则引擎需要的结构化值。"""
    normalized: dict[str, float] = {}

    payment_days = _first_number(
        [
            r"(?:结算后|验收后|完工后|交付后|月度结算后)\s*(\d+)\s*天",
            r"(\d+)\s*日内支付",
            r"(?:付款期限|账期)[^0-9]{0,6}(\d+)\s*天",
        ],
        text,
    )
    if payment_days is not None:
        normalized["payment_days"] = payment_days

    payment_ratio = _first_number(
        [
            r"(?:结算额|结算总金额|合同金额|合同额)的?\s*(\d+(?:\.\d+)?)\s*%",
            r"付款比例[^0-9]{0,6}(\d+(?:\.\d+)?)\s*%",
            r"支付(?:至|不超过)?[^0-9]{0,6}(\d+(?:\.\d+)?)\s*%",
        ],
        text,
    )
    if payment_ratio is not None:
        normalized["payment_ratio"] = payment_ratio / 100

    prepay_ratio = _first_number(
        [r"预付款(?:比例|款)?[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%"],
        text,
    )
    if prepay_ratio is not None:
        normalized["prepay_ratio"] = prepay_ratio / 100

    deposit_ratio = _first_number(
        [r"(?:履约保证金|保证金)(?:为|按|比例)?(?:合同金额的|合同额的)?[^0-9]{0,4}(\d+(?:\.\d+)?)\s*%"],
        text,
    )
    if deposit_ratio is not None:
        normalized["deposit_ratio"] = deposit_ratio / 100

    warranty_ratio = _first_number(
        [r"质保金[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%"],
        text,
    )
    if warranty_ratio is not None:
        normalized["warranty_ratio"] = warranty_ratio / 100

    tax_rate = _first_number(
        [r"(?:税率|税金)[^0-9]{0,6}(\d+(?:\.\d+)?)\s*%"],
        text,
    )
    if tax_rate is None:
        if re.search(r"施工[^0-9]{0,4}9\s*%", text):
            tax_rate = 9.0
        elif re.search(r"租赁[^0-9]{0,4}13\s*%", text):
            tax_rate = 13.0
    if tax_rate is not None:
        normalized["tax_rate"] = tax_rate

    amount_match = re.search(
        r"(?:合同金额|合同总价|合同额|签约额|总价)[^0-9]{0,10}([\d,]+(?:\.\d+)?)\s*(万元|元)",
        text,
    )
    if amount_match:
        amount = float(amount_match.group(1).replace(",", ""))
        if amount_match.group(2) == "万元":
            amount *= 10000
        normalized["contract_amount"] = amount

    quality = re.search(r"质保期[^0-9]{0,4}(\d+)\s*(个月|月|年)", text)
    if quality:
        months = int(quality.group(1))
        if quality.group(2) == "年":
            months *= 12
        normalized["quality_months"] = float(months)

    return normalized


def _page_at(text: str, offset: int) -> int:
    page = 1
    for match in PAGE_PATTERN.finditer(text[:offset]):
        page = int(match.group(1))
    return page


def _category(title: str, content: str) -> str:
    haystack = f"{title}\n{content}"
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return category
    return "other"


def _split_long(content: str, chunk_size: int = 300, overlap: int = 30) -> list[str]:
    """与掌柜智库一致的长切短合：按字符切，保留 overlap。"""
    clean = re.sub(r"\[\[PAGE=\d+\]\]", "", content).strip()
    if len(clean) <= chunk_size:
        return [clean] if clean else []
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(start + chunk_size, len(clean))
        chunks.append(clean[start:end])
        if end >= len(clean):
            break
        start = end - overlap
    return chunks


def split_clauses(text: str) -> list[dict[str, Any]]:
    matches = list(CLAUSE_PATTERN.finditer(text))
    clauses: list[dict[str, Any]] = []

    if not matches:
        content = re.sub(r"\[\[PAGE=\d+\]\]", "", text).strip()
        if not content:
            return []
        return [
            {
                "clause_no": "全文",
                "title": "全文",
                "category": _category("", content),
                "text": content,
                "page": 1,
                "char_start": 0,
                "normalized": extract_normalized(content),
                "confidence": 1.0,
            }
        ]

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw = text[start:end].strip()
        heading = match.group(1).strip()
        clause_no_match = re.match(r"(第[一二三四五六七八九十百千零〇\d]+条)", heading)
        clause_no = clause_no_match.group(1) if clause_no_match else heading
        title = heading[len(clause_no):].strip(" ：:　") or clause_no
        content = re.sub(r"\[\[PAGE=\d+\]\]", "", raw).strip()
        clauses.append(
            {
                "clause_no": clause_no,
                "title": title,
                "category": _category(title, content),
                "text": content,
                "page": _page_at(text, start),
                "char_start": start,
                "normalized": extract_normalized(content),
                "confidence": 0.9,
            }
        )
    return clauses


def extract_basic_info(text: str) -> dict[str, Any]:
    def search(pattern: str) -> str | None:
        match = re.search(pattern, text)
        return match.group(1).strip() if match else None

    normalized = extract_normalized(text)
    return {
        "contract_title": search(r"(?:合同名称|项目名称)[:：]\s*([^\n]+)"),
        "contract_no": search(r"合同编号[:：]\s*([^\s，,]+)"),
        "party_a": search(r"甲方[:：]\s*([^\n，,]+)"),
        "party_b": search(r"乙方[:：]\s*([^\n，,]+)"),
        "contract_amount": normalized.get("contract_amount"),
        "currency": "CNY",
        "sign_date": search(r"(?:签订日期|签署日期)[:：]\s*([0-9]{4}[-年/][0-9]{1,2}[-月/][0-9]{1,2})"),
        "effective_date": search(r"(?:生效日期|开始日期)[:：]\s*([0-9]{4}[-年/][0-9]{1,2}[-月/][0-9]{1,2})"),
        "expiry_date": search(r"(?:到期日期|结束日期)[:：]\s*([0-9]{4}[-年/][0-9]{1,2}[-月/][0-9]{1,2})"),
        "normalized": normalized,
    }


def parse_contract(text: str, file_name: str = "") -> dict[str, Any]:
    clauses = split_clauses(text)
    aggregated: dict[str, float] = {}
    for clause in clauses:
        aggregated.update(clause.get("normalized", {}))
    aggregated.update(extract_normalized(re.sub(r"\[\[PAGE=\d+\]\]", "", text)))

    chunks: list[dict[str, Any]] = []
    for clause in clauses:
        for piece in _split_long(clause["text"]):
            chunks.append(
                {
                    "title": clause["title"],
                    "clause_no": clause["clause_no"],
                    "page": clause["page"],
                    "content": piece,
                }
            )

    return {
        "file_name": file_name,
        "full_text": text,
        "basic_info": extract_basic_info(text),
        "normalized": aggregated,
        "clauses": clauses,
        "chunks": chunks,
    }
