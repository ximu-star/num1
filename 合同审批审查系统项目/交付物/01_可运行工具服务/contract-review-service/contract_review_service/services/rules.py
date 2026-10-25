"""
【全新代码】PG 确定性规则匹配 - 电商小二和掌柜智库都没有

功能：从 review_rules 表读规则，匹配合同解析结果
两种规则：
1. 关键词匹配：合同里出现"预付款""账期"等关键词就命中
2. 阈值比较：付款天数 > 30 天、预付款比例 > 30% 等

输出：每条命中的规则都带证据原文 + 位置 + 建议
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from contract_review_service.common.config import get_settings
from contract_review_service.models import ApprovalTask, ReviewRule

logger = logging.getLogger(__name__)

RISK_ORDER = {"low": 1, "medium": 2, "high": 3}

FIELD_LABELS = {
    "payment_days": ("付款", "账期", "结算后", "日内支付"),
    "payment_ratio": ("付款比例", "结算额的", "支付至"),
    "prepay_ratio": ("预付款",),
    "deposit_ratio": ("履约保证金", "保证金"),
    "warranty_ratio": ("质保金",),
    "tax_rate": ("税率", "税金"),
    "contract_amount": ("合同金额", "合同总价", "签约额"),
    "quality_months": ("质保期",),
}


def _compare(actual: float, operator: str, expected: float) -> bool:
    if operator == ">":
        return actual > expected
    if operator == ">=":
        return actual >= expected
    if operator == "<":
        return actual < expected
    if operator == "<=":
        return actual <= expected
    return actual == expected


def _find_clause(parse: dict[str, Any], needle: str | None) -> dict[str, Any] | None:
    if not needle:
        return None
    for clause in parse.get("clauses", []):
        if needle in clause.get("text", ""):
            return clause
    return None


def _evidence(
    parse: dict[str, Any],
    *,
    needle: str | None = None,
    field: str | None = None,
    missing: bool = False,
    max_length: int = 110,
) -> tuple[str, str]:
    """返回 (命中原文, 位置)。缺失类规则直接标注“未检索到”。"""
    if missing:
        return "未检索到相关条款", "全文"

    text = parse.get("full_text", "")
    probe = needle
    if not probe and field:
        for label in FIELD_LABELS.get(field, ()):
            if label in text:
                probe = label
                break

    clause = _find_clause(parse, probe)
    if clause and probe:
        content = clause.get("text", "")
        index = content.find(probe)
        start = max(0, index - 30)
        snippet = content[start:start + max_length].strip()
        position = f"{clause.get('clause_no', '')}（第{clause.get('page', 1)}页）"
        return snippet, position

    if probe and probe in text:
        index = text.find(probe)
        snippet = text[max(0, index - 30):index + max_length].strip()
        return snippet, "合同正文"

    return re.sub(r"\[\[PAGE=\d+\]\]", "", text)[:max_length].strip(), "合同正文"


def _match_threshold(rule: ReviewRule, normalized: dict[str, Any]) -> bool:
    if not rule.target_field or not rule.operator or rule.threshold_value is None:
        return False
    actual = normalized.get(rule.target_field)
    if actual is None:
        return False
    return _compare(float(actual), rule.operator, float(rule.threshold_value))


def _match_text_rule(rule: ReviewRule, text: str) -> bool:
    needle = rule.match_text or ""
    if not needle:
        return False
    is_missing = rule.operator == "missing"
    if rule.match_mode == "regex":
        found = re.search(needle, text) is not None
    else:
        found = needle in text
    return not found if is_missing else found


def _is_applicable(rule: ReviewRule, task: ApprovalTask) -> bool:
    if rule.rule_status != "enabled":
        return False
    if rule.applies_scope == "standard_only" and not task.is_standard_version:
        return False
    if rule.applies_scope == "nonstandard_only" and task.is_standard_version:
        return False
    return True


def _semantic_hit(rule: ReviewRule) -> dict[str, Any] | None:
    """检索手册语义参考；失败时降级跳过，不影响 PG 规则。"""
    settings = get_settings()
    if not settings.milvus_enabled:
        logger.warning("跳过语义规则 %s（Milvus 未启用）", rule.rule_code)
        return None

    query = rule.semantic_query or rule.rule_name
    try:
        from contract_review_service.services.semantic import search_manual

        results = search_manual(query)
    except Exception:
        logger.exception("语义检索失败，降级跳过规则 %s", rule.rule_code)
        return None

    if not results:
        return None

    best = results[0]
    snippet = str(best.get("text", "")).strip().replace("\n", " ")
    if len(snippet) > 300:
        snippet = snippet[:300] + "..."
    page = int(best.get("page", 0) or 0)
    return {
        "rule_id": rule.id,
        "rule_code": rule.rule_code,
        "rule_name": rule.rule_name,
        "risk_level": rule.risk_level,
        "rule_category": rule.rule_category,
        "hit_source": "milvus",
        "evidence_text": f"手册参考：{snippet}",
        "evidence_position": f"手册第{page}页" if page else "手册",
        "suggestion_text": f"{rule.suggestion_text}（手册参考相似度 {best['score']:.2f}）",
        "hit_status": "open",
        "similarity": float(best["score"]),
    }


def run_rules(
    db: Session,
    task: ApprovalTask,
    parse: dict[str, Any],
) -> list[dict[str, Any]]:
    """跑 PG 确定性规则，返回命中列表。语义规则在未启用 Milvus 时跳过。"""
    rules = db.scalars(
        select(ReviewRule).where(ReviewRule.rule_status == "enabled")
    ).all()
    normalized = parse.get("basic_info", {}).get("normalized") or parse.get("normalized") or {}
    text = parse.get("full_text", "")
    hits: list[dict[str, Any]] = []

    for rule in rules:
        if not _is_applicable(rule, task):
            continue

        hit = False
        evidence_needle: str | None = rule.match_text
        if rule.match_mode == "threshold":
            hit = _match_threshold(rule, normalized)
            evidence_needle = None
        elif rule.match_mode in {"keyword", "regex"}:
            hit = _match_text_rule(rule, text)
        elif rule.match_mode == "semantic" or rule.is_semantic:
            semantic_hit = _semantic_hit(rule)
            if semantic_hit:
                hits.append(semantic_hit)
            continue

        if not hit:
            continue

        evidence_text, position = _evidence(
            parse,
            needle=evidence_needle,
            field=rule.target_field,
            missing=rule.operator == "missing",
        )
        hits.append(
            {
                "rule_id": rule.id,
                "rule_code": rule.rule_code,
                "rule_name": rule.rule_name,
                "risk_level": rule.risk_level,
                "rule_category": rule.rule_category,
                "hit_source": "pg",
                "evidence_text": evidence_text,
                "evidence_position": position,
                "suggestion_text": rule.suggestion_text,
                "hit_status": "open",
            }
        )

    return hits


def overall_risk(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "low"
    highest = max(RISK_ORDER.get(hit["risk_level"], 1) for hit in hits)
    for level, order in RISK_ORDER.items():
        if order == highest:
            return level
    return "low"
