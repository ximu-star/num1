"""合同解析（与 contract-review-service 的 parser.py 保持同一口径）。

说明：
- 真实系统里 PDF 会走 MinerU；本微调项目只读取已经转好的 .md / .txt，
  因此这里不依赖 MinerU、数据库或 Web 服务。
- 条款切分、normalized 抽取、标题兜底逻辑与线上 parser.py 一致。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ARTICLE_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?(第[一二三四五六七八九十百千零〇\d]+条[^\n]*)"
)
SECTION_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?([一二三四五六七八九十百千零〇]+[、.．]\s*[^\n]*)"
)
ARABIC_SECTION_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?(\d{1,2}\s*[、．]\s*[^\n]*)"
)
PAREN_SECTION_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?([（(][一二三四五六七八九十百千零〇\d]+[）)]\s*[^\n]*)"
)
CLAUSE_PATTERN = ARTICLE_PATTERN
PAGE_PATTERN = re.compile(r"\[\[PAGE=(\d+)\]\]")
TITLE_PREFIX_RE = re.compile(r"^(?:工程)?(?:项目|合同)名称[:：]\s*")

CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("payment", ("付款", "支付", "账期", "预付款", "结算")),
    ("delivery", ("交付", "工期", "进场", "供货", "交货")),
    ("acceptance", ("验收", "质量", "标准")),
    ("breach", ("违约", "赔偿", "扣罚", "罚款")),
    ("confidentiality", ("保密", "数据", "信息安全")),
    ("ip", ("知识产权", "专利", "著作权")),
    ("dispute", ("争议", "管辖", "仲裁", "诉讼")),
]


def read_contract_text(path: Path) -> str:
    """读取 .md / .txt；PDF 请先在线上系统完成 MinerU 解析。"""
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8")
    raise ValueError(
        f"微调项目只直接读取 .md/.txt，收到 {suffix}。"
        "请先用线上 parse_contract/MinerU 转成 Markdown。"
    )


FULLWIDTH_TRANSLATION = str.maketrans("０１２３４５６７８９％：", "0123456789%:")


def _normalize_text(text: str) -> str:
    return text.translate(FULLWIDTH_TRANSLATION)


def _first_number_span(
    patterns: list[str],
    text: str,
    blocked_spans: list[tuple[int, int]] | None = None,
) -> tuple[float | None, tuple[int, int] | None]:
    blocked = blocked_spans or []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if any(start < match.end() and match.start() < end for start, end in blocked):
                continue
            try:
                value = float(match.group(1).replace(",", "").replace("，", ""))
            except (TypeError, ValueError):
                continue
            return value, (match.start(), match.end())
    return None, None


def _first_number(patterns: list[str], text: str) -> float | None:
    value, _ = _first_number_span(patterns, text)
    return value


def extract_normalized(text: str) -> dict[str, float]:
    """从条款原文抽取规则引擎需要的结构化值。"""
    normalized: dict[str, float] = {}
    text = _normalize_text(text)
    blocked_spans: list[tuple[int, int]] = []

    deposit_ratio, span = _first_number_span(
        [
            r"(?:履约保证金|保证金)(?:为|按|比例)?(?:合同金额的|合同额的|结算价的)?[^0-9]{0,6}(\d+(?:\.\d+)?)\s*%",
        ],
        text,
        blocked_spans,
    )
    if deposit_ratio is not None:
        normalized["deposit_ratio"] = deposit_ratio / 100
        if span:
            blocked_spans.append(span)

    warranty_ratio, span = _first_number_span(
        [r"质保金(?:为|按|比例)?(?:合同金额的|结算价的)?[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%"],
        text,
        blocked_spans,
    )
    if warranty_ratio is not None:
        normalized["warranty_ratio"] = warranty_ratio / 100
        if span:
            blocked_spans.append(span)

    prepay_ratio, span = _first_number_span(
        [r"预付(?:款)?(?:比例)?[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%"],
        text,
        blocked_spans,
    )
    if prepay_ratio is not None:
        normalized["prepay_ratio"] = prepay_ratio / 100
        if span:
            blocked_spans.append(span)
    elif re.search(r"预付(?:本合同)?(?:的)?(?:所有|全部)款项", text):
        normalized["prepay_ratio"] = 1.0

    payment_ratio, span = _first_number_span(
        [
            r"(?:结算额|结算总金额|合同金额|合同额)的?\s*(\d+(?:\.\d+)?)\s*%",
            r"(?<!预)付款比例[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%",
            r"支付(?:至|到|不超过)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%",
            r"付款至[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%",
        ],
        text,
        blocked_spans,
    )
    if payment_ratio is not None:
        normalized["payment_ratio"] = payment_ratio / 100

    payment_days = _first_number(
        [
            r"(?:结算后|验收后|完工后|竣工后|交付后|供货后)[^。；\n]{0,20}?(\d+)\s*(?:日|天)内[^。；\n]{0,20}?(?:支付|付款|付清)",
            r"(\d+)\s*(?:日|天)内(?:支付|付款|付清)",
            r"(?:付款期限|付款周期|账期)[^0-9]{0,8}(\d+)\s*(?:日|天)",
            r"(?:月结|月度结算)[^0-9]{0,8}(\d+)\s*(?:日|天)",
        ],
        text,
    )
    if payment_days is not None:
        normalized["payment_days"] = payment_days

    tax_rate = _first_number(
        [
            r"(?:税率|税金)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%",
            r"含\s*(\d+(?:\.\d+)?)\s*%\s*税",
        ],
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
        r"(?:合同金额|合同总价|合同价款|合同额|签约额|总价|暂定总价)[^0-9]{0,12}([\d,]+(?:\.\d+)?)\s*(万元|元)",
        text,
    )
    if amount_match:
        amount = float(amount_match.group(1).replace(",", "").replace("，", ""))
        if amount_match.group(2) == "万元":
            amount *= 10000
        normalized["contract_amount"] = amount

    quality = re.search(r"质保期[^0-9]{0,6}(\d+)\s*(个月|月|年)", text)
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


def _choose_headings(text: str) -> tuple[str, list[re.Match[str]]]:
    candidates: list[tuple[str, list[re.Match[str]]]] = []
    for kind, pattern in (
        ("article", ARTICLE_PATTERN),
        ("section", SECTION_PATTERN),
        ("arabic", ARABIC_SECTION_PATTERN),
        ("paren", PAREN_SECTION_PATTERN),
    ):
        matches = list(pattern.finditer(text))
        if matches:
            candidates.append((kind, matches))
    if not candidates:
        return "", []
    for preferred in ("article", "section", "arabic", "paren"):
        for kind, matches in candidates:
            if kind == preferred and len(matches) >= 2:
                return kind, matches
    kind, matches = max(candidates, key=lambda item: len(item[1]))
    return kind, matches


def _short_title(value: str, limit: int = 40) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip("，,。；;：:、 ") + "…"


def _heading_parts(kind: str, heading: str) -> tuple[str, str]:
    if kind == "article":
        match = re.match(r"(第[一二三四五六七八九十百千零〇\d]+条)", heading)
    elif kind == "section":
        match = re.match(r"([一二三四五六七八九十百千零〇]+[、.．])", heading)
    elif kind == "arabic":
        match = re.match(r"(\d{1,2}\s*[、．])", heading)
    else:
        match = re.match(r"([（(][一二三四五六七八九十百千零〇\d]+[）)])", heading)
    if not match:
        clean = heading.strip()
        return clean, clean
    clause_no = match.group(1).strip()
    title = heading[len(match.group(1)) :].strip(" ：:　") or clause_no
    return clause_no, _short_title(title)


def split_clauses(text: str) -> list[dict[str, Any]]:
    kind, matches = _choose_headings(text)
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
        clause_no, title = _heading_parts(kind, heading)
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
                "confidence": 0.9 if kind == "article" else 0.85,
            }
        )
    return clauses


def _extract_title(text: str) -> str | None:
    for pattern in (
        r"合同名称[:：]\s*([^\n]+)",
        r"(?:工程)?项目名称[:：]\s*([^\n]+)",
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        value = match.group(1).strip()
        while True:
            stripped = TITLE_PREFIX_RE.sub("", value)
            if stripped == value:
                break
            value = stripped.strip()
        value = re.split(r"[，,。；;\n]", value)[0].strip(" \\_#\t")
        if 2 <= len(value) <= 80:
            return value

    for line in text.splitlines():
        clean = re.sub(r"\[\[PAGE=\d+\]\]", "", line).strip(" #\t")
        if 2 <= len(clean) <= 40 and not re.search(r"[:：]", clean):
            return clean
    return None


def extract_basic_info(text: str) -> dict[str, Any]:
    def search(pattern: str) -> str | None:
        match = re.search(pattern, text)
        return match.group(1).strip() if match else None

    normalized = extract_normalized(text)
    return {
        "contract_title": _extract_title(text),
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
