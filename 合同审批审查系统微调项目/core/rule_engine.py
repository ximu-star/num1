"""确定性规则引擎（与 contract-review-service 的 rules.py 同一口径）。

只做 keyword / regex / threshold / missing 四类匹配；语义规则不在这里执行，
而是作为 semantic_candidates 交给模型判断。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import config as C

FIELD_LABELS = {
    "payment_days": ("付款", "账期", "结算后", "日内支付"),
    "payment_ratio": ("付款比例", "结算额的", "支付至", "付款至"),
    "prepay_ratio": ("预付本合同", "预付全部", "预付所有", "预付款", "预付"),
    "deposit_ratio": ("履约保证金", "保证金"),
    "warranty_ratio": ("质保金",),
    "tax_rate": ("税率", "税金", "含税"),
    "contract_amount": ("合同金额", "合同总价", "合同价款", "暂定总价", "签约额"),
    "quality_months": ("质保期",),
}
PERCENT_FIELDS = {"payment_ratio", "prepay_ratio", "deposit_ratio", "warranty_ratio"}
SENTENCE_RE = re.compile(r"[^。；;\n]+[。；;]?")


def load_rules(path: Path = C.RULES_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"规则库为空或格式错误: {path}")
    return [rule for rule in rules if rule.get("rule_status", "enabled") == "enabled"]


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


def _match_threshold(rule: dict[str, Any], normalized: dict[str, Any]) -> bool:
    field = rule.get("target_field")
    operator = rule.get("operator")
    threshold = rule.get("threshold_value")
    if not field or not operator or threshold is None:
        return False
    actual = normalized.get(field)
    if actual is None:
        return False
    return _compare(float(actual), operator, float(threshold))


def _match_text_rule(rule: dict[str, Any], text: str) -> bool:
    needle = rule.get("match_text") or ""
    if not needle:
        return False
    if rule.get("match_mode") == "regex":
        found = re.search(needle, text) is not None
    else:
        found = needle in text
    return not found if rule.get("operator") == "missing" else found


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _value_keys(field: str | None, value: float | None) -> list[str]:
    if value is None:
        return []
    if field in PERCENT_FIELDS:
        number = _format_number(value * 100)
        return [f"{number}%", f"{number}％", f"{number} %"]
    if field == "tax_rate":
        number = _format_number(value)
        return [f"{number}%", f"{number}％", f"{number} %"]
    if field == "payment_days":
        number = _format_number(value)
        return [f"{number}日", f"{number}天", f"{number} 日", f"{number} 天"]
    if field == "quality_months":
        number = _format_number(value)
        return [f"{number}个月", f"{number}月", f"{number} 个月"]
    if field == "contract_amount":
        return [f"{value:,.2f}", f"{value:.2f}", f"{value:,.0f}", str(int(round(value)))]
    return [_format_number(value)]


def _clean_text(text: str) -> str:
    text = re.sub(r"\[\[PAGE=\d+\]\]", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    return text


def _sentences(text: str) -> list[str]:
    pieces: list[str] = []
    for match in SENTENCE_RE.finditer(_clean_text(text)):
        piece = match.group(0).strip(" \t#")
        if len(piece) >= 2:
            pieces.append(piece)
    return pieces


def _pick_sentence(sentences: list[str], labels: list[str], keys: list[str]) -> str | None:
    if keys:
        for sentence in sentences:
            if any(key in sentence for key in keys) and any(label in sentence for label in labels):
                return sentence
        for sentence in sentences:
            if any(key in sentence for key in keys):
                return sentence
    for sentence in sentences:
        if any(label in sentence for label in labels):
            return sentence
    return None


def _trim(sentence: str, anchors: list[str], max_length: int) -> str:
    if len(sentence) <= max_length:
        return sentence
    index = -1
    for anchor in anchors:
        position = sentence.find(anchor)
        if position >= 0 and (index < 0 or position < index):
            index = position
    if index < 0:
        return sentence[:max_length].rstrip() + "…"
    start = max(0, index - max_length // 3)
    snippet = sentence[start : start + max_length].rstrip()
    if start > 0:
        snippet = "…" + snippet
    if start + max_length < len(sentence):
        snippet += "…"
    return snippet


def _evidence(
    parse: dict[str, Any],
    *,
    needle: str | None = None,
    field: str | None = None,
    value: float | None = None,
    missing: bool = False,
    max_length: int = 110,
) -> tuple[str, str]:
    if missing:
        return C.MISSING_EVIDENCE_TEXT, "全文"

    labels: list[str] = []
    if needle:
        labels.append(needle)
    if field:
        labels.extend(FIELD_LABELS.get(field, ()))
    keys = _value_keys(field, value)
    anchors = keys + labels

    clause = None
    for candidate in parse.get("clauses", []):
        content = candidate.get("text", "")
        if any(anchor in content for anchor in anchors):
            clause = candidate
            break

    if clause:
        sentence = _pick_sentence(_sentences(clause.get("text", "")), labels, keys)
        if sentence:
            return (
                _trim(sentence, anchors, max_length),
                f"{clause.get('clause_no', '')}（第{clause.get('page', 1)}页）",
            )

    text = parse.get("full_text", "")
    sentence = _pick_sentence(_sentences(text), labels, keys)
    if sentence:
        return _trim(sentence, anchors, max_length), "合同正文"
    return _clean_text(text)[:max_length].strip(), "合同正文"


def _is_applicable(rule: dict[str, Any], is_standard_version: bool) -> bool:
    scope = rule.get("applies_scope", "all")
    if scope == "standard_only" and not is_standard_version:
        return False
    if scope == "nonstandard_only" and is_standard_version:
        return False
    return True


def run_rules(
    parsed: dict[str, Any],
    rules: list[dict[str, Any]] | None = None,
    *,
    is_standard_version: bool = False,
) -> list[dict[str, Any]]:
    """返回确定性规则命中列表（不含语义规则）。"""
    rules = rules if rules is not None else load_rules()
    normalized = parsed.get("basic_info", {}).get("normalized") or parsed.get("normalized") or {}
    text = parsed.get("full_text", "")
    hits: list[dict[str, Any]] = []

    for rule in rules:
        if rule.get("is_semantic") or rule.get("match_mode") == "semantic":
            continue
        if not _is_applicable(rule, is_standard_version):
            continue

        evidence_needle: str | None = rule.get("match_text")
        evidence_value: float | None = None
        if rule.get("match_mode") == "threshold":
            hit = _match_threshold(rule, normalized)
            evidence_needle = None
            actual = normalized.get(rule.get("target_field") or "")
            evidence_value = float(actual) if actual is not None else None
        else:
            hit = _match_text_rule(rule, text)

        if not hit:
            continue

        evidence_text, position = _evidence(
            parsed,
            needle=evidence_needle,
            field=rule.get("target_field"),
            value=evidence_value,
            missing=rule.get("operator") == "missing",
        )
        hits.append(
            {
                "rule_code": rule["rule_code"],
                "rule_name": rule["rule_name"],
                "rule_category": rule["rule_category"],
                "risk_level": rule["risk_level"],
                "hit_source": "pg",
                "evidence_text": evidence_text,
                "evidence_position": position,
                "suggestion_text": rule.get("suggestion_text", ""),
            }
        )
    return hits


def semantic_rules(rules: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rules = rules if rules is not None else load_rules()
    return [rule for rule in rules if rule.get("is_semantic")]


def overall_risk(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "low"
    highest = max(C.RISK_ORDER.get(hit["risk_level"], 1) for hit in hits)
    for level, order in C.RISK_ORDER.items():
        if order == highest:
            return level
    return "low"
