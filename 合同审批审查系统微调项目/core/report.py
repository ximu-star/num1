"""审查报告的 gold 构造、校验与事实检查。

核心原则：
- 结构化字段（风险等级、命中规则、语义判断）由规则引擎和种子 gold 决定；
- 自然语言（summary / focus_points / semantic reason）可以交给 LLM 润色，
  但必须通过事实检查，不能出现输入里没有的规则号、金额、比例、天数。
"""

from __future__ import annotations

import json
import re
from typing import Any

import config as C

RULE_CODE_RE = re.compile(r"R-[A-Z]+-\d+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
CLAUSE_NO_RE = re.compile(r"第[一二三四五六七八九十百千零〇\d]+条")
POSITION_RE = re.compile(r"^.+?（第\d+页）$")
RISK_CN = {"low": "低", "medium": "中", "high": "高"}


def _risk_max(levels: list[str]) -> str:
    if not levels:
        return "low"
    highest = max(C.RISK_ORDER.get(level, 1) for level in levels)
    for level, order in C.RISK_ORDER.items():
        if order == highest:
            return level
    return "low"


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _clause_texts(snapshot: dict[str, Any]) -> list[str]:
    return [str(clause.get("text", "")) for clause in snapshot.get("clauses", [])]


def _valid_positions(snapshot: dict[str, Any]) -> set[str]:
    positions = {"全文", "合同正文"}
    for clause in snapshot.get("clauses", []):
        positions.add(f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）")
    return positions


def _trim_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fallback_summary(
    snapshot: dict[str, Any],
    risk: str,
    semantic_labels: list[dict[str, Any]] | None = None,
) -> str:
    hits = snapshot.get("rule_hits", [])
    semantic_hits = [item for item in (semantic_labels or []) if item.get("hit")]
    if not hits and not semantic_hits:
        return (
            f"本合同未命中确定性规则和语义候选，整体风险等级为{RISK_CN.get(risk, risk)}。"
            "建议仍由业务人员复核条款完整性与签署授权。"
        )
    counts = {level: 0 for level in C.RISK_ORDER}
    for hit in hits:
        counts[hit["risk_level"]] = counts.get(hit["risk_level"], 0) + 1
    names = "、".join(hit["rule_name"] for hit in hits[:4]) or "无确定性规则"
    semantic_text = ""
    if semantic_hits:
        semantic_names = "、".join(item["rule_code"] for item in semantic_hits)
        semantic_text = (
            f"另有 {len(semantic_hits)} 条语义规则命中（{semantic_names}），"
            "需结合条款原文复核。"
        )
    return (
        f"本合同共命中 {len(hits)} 条确定性规则，其中高风险 {counts.get('high', 0)} 条、"
        f"中风险 {counts.get('medium', 0)} 条、低风险 {counts.get('low', 0)} 条，"
        f"整体风险等级为{RISK_CN.get(risk, risk)}。{semantic_text}"
        f"主要风险集中在{names}，建议按关注点逐条复核并在审批意见中说明处理方式。"
    )


GENERIC_FOCUS_POINTS = [
    "复核合同签署主体、用印类型与授权文件是否齐全。",
    "复核付款节点、结算资料与发票条款是否可执行。",
    "复核争议解决、送达地址与合同解除条件。",
    "复核合同附件、工程量清单与计价口径是否一致。",
]

SUMMARY_PADDING = [
    "建议复核合同主体、用印类型与授权文件是否齐全。",
    "建议复核付款节点、结算资料与发票条款是否可执行。",
    "建议复核争议解决、送达地址与合同解除条件。",
    "建议复核合同附件、工程量清单与计价口径是否一致。",
]


def _fit_summary(summary: str, risk: str) -> str:
    """把 summary 稳定压到 [MIN_SUMMARY_CHARS, MAX_SUMMARY_CHARS]。

    老师原实现没有长度兜底：无命中/少量命中时模板摘要可能不到 80 字，
    导致 validate_report 把 fallback gold 自己判失败。这里只补通用复核句，
    不引入数字、规则号或合同事实。
    """
    text = re.sub(r"\s+", " ", str(summary)).strip()
    if not text:
        text = f"整体风险等级为{RISK_CN.get(risk, risk)}。"

    if len(text) > C.MAX_SUMMARY_CHARS:
        cut = text[: C.MAX_SUMMARY_CHARS]
        boundary = max(
            cut.rfind("。"), cut.rfind("；"), cut.rfind(";"),
            cut.rfind("！"), cut.rfind("？"),
        )
        text = cut[: boundary + 1] if boundary >= C.MIN_SUMMARY_CHARS else cut

    for sentence in SUMMARY_PADDING:
        if len(text) >= C.MIN_SUMMARY_CHARS:
            break
        if sentence not in text:
            text = (text + sentence).strip()

    if len(text) < C.MIN_SUMMARY_CHARS:
        text = (text + "建议业务人员结合合同原文逐条复核并记录处理意见。").strip()
    if len(text) > C.MAX_SUMMARY_CHARS:
        text = text[: C.MAX_SUMMARY_CHARS].rstrip("，,；;：: ") + "…"
    return text


def _focus_ref(kind: str, rule_code: str = "", evidence_position: str = "") -> dict[str, str]:
    """关注点出处：rule=确定性规则，semantic=语义规则，general=通用复核项。"""
    return {
        "kind": kind,
        "rule_code": rule_code,
        "evidence_position": evidence_position,
    }


def fallback_focus_points(
    snapshot: dict[str, Any],
    semantic_labels: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, str]]]:
    """返回 (关注点文本, 关注点出处)，两者一一对应。

    老师原实现只输出字符串列表；这里增加 focus_point_refs，
    让每条关注点都能回溯到 rule_hit / semantic_judgement。
    """
    items: list[tuple[str, str, dict[str, str]]] = []
    for hit in snapshot.get("rule_hits", []):
        items.append(
            (
                hit["risk_level"],
                _trim_text(
                    f"{hit['rule_name']}（{hit['rule_code']}）：{hit['suggestion_text']}",
                    C.MAX_FOCUS_POINT_CHARS,
                ),
                _focus_ref("rule", hit["rule_code"], hit["evidence_position"]),
            )
        )
    for label in semantic_labels:
        if not label.get("hit"):
            continue
        items.append(
            (
                label["risk_level"],
                _trim_text(
                    f"{label['rule_code']}：{label.get('reason', '语义规则命中')}",
                    C.MAX_FOCUS_POINT_CHARS,
                ),
                _focus_ref("semantic", label["rule_code"], label.get("evidence_position", "全文")),
            )
        )
    # 按风险从高到低稳定排序，避免高风险项被截断。
    items.sort(key=lambda item: -C.RISK_ORDER.get(item[0], 1))
    points = [text for _, text, _ in items]
    refs = [ref for _, _, ref in items]
    # 规则命中较少时补足通用复核项，保证 3~5 条且不引入合同事实。
    for generic in GENERIC_FOCUS_POINTS:
        if len(points) >= C.MIN_FOCUS_POINTS:
            break
        if generic not in points:
            points.append(generic)
            refs.append(_focus_ref("general"))
    return points[: C.MAX_FOCUS_POINTS], refs[: C.MAX_FOCUS_POINTS]


def build_gold_report(
    snapshot: dict[str, Any],
    seed: dict[str, Any],
    narrative: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hit_rules = [
        {
            "rule_code": hit["rule_code"],
            "rule_name": hit["rule_name"],
            "risk_level": hit["risk_level"],
            "evidence_text": hit["evidence_text"],
            "evidence_position": hit["evidence_position"],
            "advice": hit["suggestion_text"],
        }
        for hit in snapshot.get("rule_hits", [])
    ]
    expected_semantic = seed.get("expected_semantic") or []
    semantic_judgements = [
        {
            "rule_code": label["rule_code"],
            "hit": bool(label["hit"]),
            "risk_level": label["risk_level"],
            "reason": label.get("reason", ""),
            "evidence_position": label.get("evidence_position", "全文"),
            "evidence_quote": label.get("evidence_quote", ""),
        }
        for label in expected_semantic
    ]
    risk = _risk_max(
        [hit["risk_level"] for hit in hit_rules]
        + [item["risk_level"] for item in semantic_judgements if item["hit"]]
    )
    summary = fallback_summary(snapshot, risk, expected_semantic)
    # 关注点文本可交给 LLM 润色；出处 refs 必须来自规则/语义 gold，不能由模型编。
    focus_points, focus_point_refs = fallback_focus_points(snapshot, expected_semantic)

    if narrative:
        if isinstance(narrative.get("summary"), str) and narrative["summary"].strip():
            summary = narrative["summary"].strip()
        if isinstance(narrative.get("focus_points"), list):
            cleaned = [
                str(point).strip()
                for point in narrative["focus_points"]
                if isinstance(point, str) and point.strip()
            ]
            # 只有数量与 gold refs 一致时才替换，保证“关注点 -> 出处”不会错位。
            if cleaned and len(cleaned) == len(focus_point_refs):
                focus_points = cleaned[: C.MAX_FOCUS_POINTS]
        reasons = narrative.get("semantic_reasons")
        if isinstance(reasons, dict):
            for item in semantic_judgements:
                reason = reasons.get(item["rule_code"])
                if isinstance(reason, str) and reason.strip():
                    item["reason"] = reason.strip()

    summary = _fit_summary(summary, risk)
    return {
        "overall_risk_level": risk,
        "hit_rules": hit_rules,
        "summary": summary,
        "focus_points": focus_points,
        "focus_point_refs": focus_point_refs,
        "semantic_judgements": semantic_judgements,
    }


def build_semantic_output(
    seed: dict[str, Any],
    reasons: dict[str, str] | None = None,
) -> dict[str, Any]:
    reasons = reasons or {}
    judgements = []
    for label in seed.get("expected_semantic") or []:
        reason = reasons.get(label["rule_code"]) or label.get("reason", "")
        judgements.append(
            {
                "rule_code": label["rule_code"],
                "hit": bool(label["hit"]),
                "risk_level": label["risk_level"],
                "reason": str(reason).strip(),
                "evidence_position": label.get("evidence_position", "全文"),
                "evidence_quote": label.get("evidence_quote", ""),
            }
        )
    return {"semantic_judgements": judgements}


MISSING_FIELD_CN = {
    "contract_amount": "合同金额",
    "payment_days": "付款天数",
    "payment_ratio": "付款比例",
    "prepay_ratio": "预付款比例",
    "deposit_ratio": "履约保证金比例",
    "warranty_ratio": "质保金比例",
    "tax_rate": "税率",
}

REJECT_NEEDED = {
    "parse_failed": [
        "可复制文字的合同正文或清晰扫描件",
        "合同关键页（金额、付款、违约、签署页）",
        "如为扫描件，请确认已通过 OCR 识别",
    ],
    "insufficient": [
        "完整合同正文，至少包含金额、付款、结算条款",
        "合同金额、付款比例、付款天数等关键字段",
        "如为补充协议，请同时提供原合同对应条款",
    ],
    "out_of_scope": [
        "确认该合同是否属于工程/租赁/分包审查范围",
        "如属于审查范围，请提供合同正文与关键页",
    ],
    "semantic_unknown": [
        "语义规则所需的支撑材料（如成本分析表/测算表）",
        "对应条款的完整上下文",
        "如无法提供，请确认本次只做确定性规则审查",
    ],
}


def build_reject_output(snapshot: dict[str, Any]) -> dict[str, Any]:
    """拒答输出：按 reject_type 给出原因和需要补充的材料。

    老师框架里的 reject 只有“解析失败”一种；这里扩展为四类，
    对应 fidelity/refusal/provenance 中的 refusal 维度。
    """
    reject_type = snapshot.get("reject_type") or "parse_failed"
    if reject_type not in C.REJECT_TYPES:
        reject_type = "parse_failed"
    reason = (
        snapshot.get("reject_reason")
        or snapshot.get("parse_error")
        or "合同内容不完整，无法进行规则审查。"
    )
    missing_fields = snapshot.get("missing_fields") or []
    if reject_type == "insufficient" and missing_fields:
        labels = [MISSING_FIELD_CN.get(str(field), str(field)) for field in missing_fields]
        reason = f"{reason}（缺少字段：{'、'.join(labels)}）"
    return {
        "status": "rejected",
        "reject_type": reject_type,
        "reason": reason,
        "needed": list(REJECT_NEEDED[reject_type]),
    }


def _number_variants(value: str) -> set[str]:
    """把同一个数值的不同写法都加入白名单（1000000 / 1,000,000 / 100万 / 0.55 / 55）。"""
    cleaned = value.replace(",", "").replace("，", "")
    variants = {cleaned}
    try:
        number = float(cleaned)
    except ValueError:
        return variants
    if abs(number - round(number)) < 1e-9:
        variants.add(str(int(round(number))))
    variants.add(f"{number:g}")
    if 0 <= number <= 1:
        variants.add(f"{number * 100:g}")
    if number >= 10000:
        variants.add(f"{number / 10000:g}")
    return variants


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _allowed_numbers(snapshot: dict[str, Any], seed: dict[str, Any], report: dict[str, Any]) -> set[str]:
    # 白名单只来自输入快照，不把 gold/expected_report 混进来。
    text = json.dumps(snapshot, ensure_ascii=False).replace(",", "").replace("，", "")
    allowed: set[str] = set()
    for number in NUMBER_RE.findall(text):
        allowed.update(_number_variants(number))
    allowed.update(CLAUSE_NO_RE.findall(text))

    hits = _as_list(report.get("hit_rules"))
    judgements = _as_list(report.get("semantic_judgements"))
    allowed.update({str(len(hits)), str(len(judgements)), str(len(_as_list(report.get("focus_points"))))})
    for level in C.RISK_ORDER:
        allowed.add(str(sum(1 for hit in hits if isinstance(hit, dict) and hit.get("risk_level") == level)))
    return allowed


def fact_violations(
    text: str,
    snapshot: dict[str, Any],
    seed: dict[str, Any],
    report: dict[str, Any] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """返回 (输入之外的规则号, 输入之外的数字, 输入之外的条款号)。"""
    if not isinstance(text, str):
        return [], [], []
    allowed_codes = {hit["rule_code"] for hit in snapshot.get("rule_hits", [])}
    allowed_codes.update(item["rule_code"] for item in snapshot.get("semantic_candidates", []))
    used_codes = set(RULE_CODE_RE.findall(text))
    invented_codes = sorted(used_codes - allowed_codes)

    cleaned = text.replace(",", "").replace("，", "")
    allowed_numbers = _allowed_numbers(snapshot, seed, report or {"hit_rules": []})
    invented_numbers = sorted(
        {number for number in NUMBER_RE.findall(cleaned) if number not in allowed_numbers}
    )

    allowed_clauses = set(CLAUSE_NO_RE.findall(json.dumps(snapshot, ensure_ascii=False)))
    invented_clauses = sorted(set(CLAUSE_NO_RE.findall(text)) - allowed_clauses)
    return invented_codes, invented_numbers, invented_clauses


def _fact_errors(text: str, snapshot: dict[str, Any], seed: dict[str, Any], report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    invented_codes, invented_numbers, invented_clauses = fact_violations(text, snapshot, seed, report)
    if invented_codes:
        errors.append(f"出现输入之外的规则号: {invented_codes}")
    if invented_numbers:
        errors.append(f"出现输入之外的数字: {invented_numbers[:8]}")
    if invented_clauses:
        errors.append(f"出现输入之外的条款号: {invented_clauses[:8]}")
    return errors


def fact_errors(
    text: str,
    snapshot: dict[str, Any],
    seed: dict[str, Any],
    report: dict[str, Any] | None = None,
) -> list[str]:
    """公开版事实检查：规则号、金额、比例、天数、条款号是否越界。"""
    return _fact_errors(text, snapshot, seed, report or {"hit_rules": []})


def _position_clause_map(snapshot: dict[str, Any]) -> dict[str, str]:
    return {
        f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）": _normalize_ws(
            str(clause.get("text", ""))
        )
        for clause in snapshot.get("clauses", [])
    }


def _summary_consistency(summary: str, report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    hits = [hit for hit in _as_list(report.get("hit_rules")) if isinstance(hit, dict)]
    semantics = [
        item for item in _as_list(report.get("semantic_judgements"))
        if isinstance(item, dict)
    ]
    risk_counts = {level: 0 for level in C.RISK_ORDER}
    for hit in hits:
        level = hit.get("risk_level", "low")
        risk_counts[level] = risk_counts.get(level, 0) + 1

    overall = report.get("overall_risk_level")
    for level, cn in RISK_CN.items():
        if f"风险等级为{cn}" in summary and overall != level:
            errors.append(f"summary 说整体风险为{cn}，但 overall_risk_level={overall}")
    matched = re.search(r"共命中\s*(\d+)\s*条", summary)
    if matched and int(matched.group(1)) != len(hits):
        errors.append(f"summary 命中条数 {matched.group(1)} 与 hit_rules {len(hits)} 不一致")
    for level, cn in RISK_CN.items():
        matched = re.search(rf"{cn}风险\s*(\d+)\s*条", summary)
        if matched and int(matched.group(1)) != risk_counts.get(level, 0):
            errors.append(f"summary {cn}风险条数与命中结果不一致")
    if "未命中" in summary and hits:
        errors.append("summary 说未命中，但 hit_rules 非空")
    matched = re.search(r"(?:另有\s*)?(\d+)\s*条语义规则命中", summary)
    if matched:
        expected = sum(1 for item in semantics if item.get("hit"))
        if int(matched.group(1)) != expected:
            errors.append("summary 语义命中条数与 semantic_judgements 不一致")
    return errors


def validate_focus_point_refs(
    refs: Any,
    focus_points: list[Any],
    snapshot: dict[str, Any],
) -> list[str]:
    """校验每条 focus_point 都有可回溯的出处。

    这是 provenance 维度的核心：关注点可以归纳，但必须能回到
    某条 rule_hit / semantic_candidate；通用复核项允许 kind=general。
    """
    errors: list[str] = []
    if not isinstance(refs, list):
        return ["focus_point_refs 必须是列表"]
    if len(refs) != len(focus_points):
        errors.append("focus_point_refs 数量必须与 focus_points 一致")
    input_hits = {hit["rule_code"]: hit for hit in snapshot.get("rule_hits", [])}
    input_semantics = {
        item["rule_code"]: item for item in snapshot.get("semantic_candidates", [])
    }
    valid_positions = _valid_positions(snapshot)
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            errors.append(f"focus_point_refs[{index}] 必须是对象")
            continue
        kind = ref.get("kind")
        code = ref.get("rule_code")
        position = ref.get("evidence_position")
        if kind == "rule":
            source = input_hits.get(code)
            if source is None:
                errors.append(f"focus_point_refs[{index}] 的 rule_code 不在输入 rule_hits 内")
            elif position != source.get("evidence_position"):
                errors.append(f"focus_point_refs[{index}] 的 evidence_position 与规则命中不一致")
        elif kind == "semantic":
            if code not in input_semantics:
                errors.append(f"focus_point_refs[{index}] 的 rule_code 不在 semantic_candidates 内")
            elif position not in valid_positions:
                errors.append(f"focus_point_refs[{index}] 的 evidence_position 非法")
        elif kind == "general":
            if code or position:
                errors.append(f"focus_point_refs[{index}] 为 general 时不应带规则号/位置")
        else:
            errors.append(f"focus_point_refs[{index}] 的 kind 非法: {kind!r}")
    return errors


def validate_report(
    report: dict[str, Any],
    snapshot: dict[str, Any],
    seed: dict[str, Any],
) -> list[str]:
    if not isinstance(report, dict):
        return ["报告必须是 JSON 对象"]
    errors: list[str] = []
    for field in (
        "overall_risk_level", "hit_rules", "summary",
        "focus_points", "focus_point_refs", "semantic_judgements",
    ):
        if field not in report:
            errors.append(f"缺少字段 {field}")

    summary = report.get("summary") if isinstance(report.get("summary"), str) else ""
    focus_points = report.get("focus_points") if isinstance(report.get("focus_points"), list) else []
    focus_point_refs = report.get("focus_point_refs") if isinstance(report.get("focus_point_refs"), list) else []
    output_hits = report.get("hit_rules") if isinstance(report.get("hit_rules"), list) else []
    sem_judgements = (
        report.get("semantic_judgements")
        if isinstance(report.get("semantic_judgements"), list)
        else []
    )
    if not isinstance(report.get("hit_rules"), list):
        errors.append("hit_rules 必须是列表")
    if not isinstance(report.get("focus_points"), list):
        errors.append("focus_points 必须是列表")
    if not isinstance(report.get("focus_point_refs"), list):
        errors.append("focus_point_refs 必须是列表")
    if not isinstance(report.get("semantic_judgements"), list):
        errors.append("semantic_judgements 必须是列表")

    input_hits = {hit["rule_code"]: hit for hit in snapshot.get("rule_hits", [])}
    output_codes = [
        hit.get("rule_code")
        for hit in output_hits
        if isinstance(hit, dict) and isinstance(hit.get("rule_code"), str)
    ]
    if set(output_codes) != set(input_hits):
        errors.append(f"hit_rules 规则集合不一致: {sorted(set(output_codes))} != {sorted(input_hits)}")
    if len(output_codes) != len(set(output_codes)):
        errors.append("hit_rules 存在重复规则")

    position_map = _position_clause_map(snapshot)
    for hit in output_hits:
        if not isinstance(hit, dict):
            errors.append("hit_rules 元素必须是对象")
            continue
        code = hit.get("rule_code")
        source = input_hits.get(code)
        if source is None:
            continue
        for field in ("rule_name", "risk_level", "evidence_text", "evidence_position", "advice"):
            if field not in hit:
                errors.append(f"{code} 缺少字段 {field}")
        expected_position = source.get("evidence_position")
        if hit.get("evidence_position") != expected_position:
            errors.append(f"{code} 的 evidence_position 与输入不一致: {hit.get('evidence_position')!r}")
        evidence = _normalize_ws(str(hit.get("evidence_text", "")))
        source_evidence = _normalize_ws(str(source.get("evidence_text", "")))
        if evidence == C.MISSING_EVIDENCE_TEXT or source_evidence == C.MISSING_EVIDENCE_TEXT:
            if evidence != source_evidence:
                errors.append(f"{code} 的缺失类证据必须写“{C.MISSING_EVIDENCE_TEXT}”")
        else:
            clause_text = position_map.get(str(expected_position))
            if clause_text is not None:
                if evidence not in clause_text:
                    errors.append(f"{code} 的 evidence_text 不在该位置对应条款中")
            elif evidence != source_evidence:
                errors.append(f"{code} 的 evidence_text 无法在条款原文中溯源")
        if hit.get("risk_level") != source.get("risk_level"):
            errors.append(f"{code} 的 risk_level 与输入不一致")
        if hit.get("rule_name") != source.get("rule_name"):
            errors.append(f"{code} 的 rule_name 与输入不一致")

    if report.get("overall_risk_level") not in C.RISK_ORDER:
        errors.append(f"overall_risk_level 非法: {report.get('overall_risk_level')!r}")
    expected_overall = _risk_max(
        [hit.get("risk_level", "low") for hit in output_hits if isinstance(hit, dict)]
        + [
            item.get("risk_level", "low")
            for item in sem_judgements
            if isinstance(item, dict) and item.get("hit")
        ]
    )
    if report.get("overall_risk_level") != expected_overall:
        errors.append(
            f"overall_risk_level 应为 {expected_overall}，实际为 {report.get('overall_risk_level')}"
        )

    if not (C.MIN_SUMMARY_CHARS <= len(summary.strip()) <= C.MAX_SUMMARY_CHARS):
        errors.append(f"summary 长度必须在 {C.MIN_SUMMARY_CHARS}~{C.MAX_SUMMARY_CHARS} 字")
    if not (C.MIN_FOCUS_POINTS <= len(focus_points) <= C.MAX_FOCUS_POINTS):
        errors.append(f"focus_points 必须有 {C.MIN_FOCUS_POINTS}~{C.MAX_FOCUS_POINTS} 条")
    else:
        for point in focus_points:
            if not isinstance(point, str) or len(point) > C.MAX_FOCUS_POINT_CHARS:
                errors.append(f"focus_point 超长或非字符串: {str(point)[:20]!r}")
    errors.extend(validate_focus_point_refs(focus_point_refs, focus_points, snapshot))

    errors.extend(validate_semantic_judgements(sem_judgements, snapshot, seed))
    errors.extend(_summary_consistency(summary, report))
    errors.extend(_fact_errors(summary, snapshot, seed, report))
    errors.extend(
        _fact_errors(
            " ".join(point for point in focus_points if isinstance(point, str)),
            snapshot,
            seed,
            report,
        )
    )
    semantic_reasons = " ".join(
        str(item.get("reason", ""))
        for item in sem_judgements
        if isinstance(item, dict)
    )
    errors.extend(_fact_errors(semantic_reasons, snapshot, seed, report))
    return errors


def validate_semantic_judgements(
    judgements: Any,
    snapshot: dict[str, Any],
    seed: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(judgements, list):
        return ["semantic_judgements 必须是列表"]
    input_items = {item["rule_code"]: item for item in snapshot.get("semantic_candidates", [])}
    expected = {item["rule_code"]: item for item in seed.get("expected_semantic", [])}
    output_codes = [
        item.get("rule_code")
        for item in judgements
        if isinstance(item, dict) and isinstance(item.get("rule_code"), str)
    ]
    if set(output_codes) != set(input_items):
        errors.append(f"semantic_judgements 规则集合不一致: {sorted(set(output_codes))} != {sorted(input_items)}")
    clause_texts = [_normalize_ws(text) for text in _clause_texts(snapshot)]
    valid_positions = _valid_positions(snapshot)
    valid_positions.update(str(label.get("evidence_position")) for label in expected.values())
    for item in judgements:
        if not isinstance(item, dict):
            errors.append("semantic_judgements 元素必须是对象")
            continue
        code = item.get("rule_code")
        if code not in input_items:
            continue
        if not isinstance(item.get("hit"), bool):
            errors.append(f"{code} 的 hit 必须是布尔值")
        if item.get("risk_level") != input_items[code].get("risk_level"):
            errors.append(f"{code} 的 risk_level 与输入候选不一致")
        if item.get("evidence_position") not in valid_positions:
            errors.append(f"{code} 的 evidence_position 非法")
        quote = _normalize_ws(str(item.get("evidence_quote", "")))
        expected_quote = _normalize_ws(str(expected.get(code, {}).get("evidence_quote", "")))
        traceable = any(quote in text for text in clause_texts) if quote else False
        if quote and not traceable and quote != expected_quote:
            errors.append(f"{code} 的 evidence_quote 无法在条款原文中溯源")
        if item.get("hit") and not quote:
            errors.append(f"{code} 命中时必须引用条款原文")
        if code in expected:
            if bool(item.get("hit")) != bool(expected[code]["hit"]):
                errors.append(f"{code} 的 hit 与 gold 不一致")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            errors.append(f"{code} 缺少 reason")
    return errors


def validate_semantic_output(report: dict[str, Any], snapshot: dict[str, Any], seed: dict[str, Any]) -> list[str]:
    if not isinstance(report, dict):
        return ["semantic 输出必须是 JSON 对象"]
    if "semantic_judgements" not in report:
        return ["缺少 semantic_judgements"]
    errors = validate_semantic_judgements(report["semantic_judgements"], snapshot, seed)
    errors.extend(_fact_errors(json.dumps(report, ensure_ascii=False), snapshot, seed, {"hit_rules": []}))
    return errors


def validate_reject_output(report: dict[str, Any]) -> list[str]:
    if not isinstance(report, dict):
        return ["reject 输出必须是 JSON 对象"]
    errors: list[str] = []
    if report.get("status") != "rejected":
        errors.append("reject 样本必须输出 status=rejected")
    if report.get("reject_type") not in C.REJECT_TYPES:
        errors.append(f"reject_type 必须是 {list(C.REJECT_TYPES)} 之一")
    if not isinstance(report.get("reason"), str) or not report["reason"].strip():
        errors.append("reject 样本必须说明原因")
    needed = report.get("needed")
    if not isinstance(needed, list) or not needed:
        errors.append("reject 样本必须列出需要补充的材料")
    for forbidden in (
        "hit_rules", "summary", "focus_points", "focus_point_refs", "overall_risk_level"
    ):
        if forbidden in report:
            errors.append(f"reject 样本不得输出 {forbidden}")
    return errors


def validate_narrative(
    narrative: dict[str, Any],
    snapshot: dict[str, Any],
    seed: dict[str, Any],
    gold: dict[str, Any],
) -> list[str]:
    if not isinstance(narrative, dict):
        return ["自然语言结果必须是 JSON 对象"]
    errors: list[str] = []
    summary = narrative.get("summary")
    if not isinstance(summary, str) or not (C.MIN_SUMMARY_CHARS <= len(summary.strip()) <= C.MAX_SUMMARY_CHARS):
        errors.append("summary 缺失或长度不合规")
    focus_points = narrative.get("focus_points")
    if not isinstance(focus_points, list) or not (
        C.MIN_FOCUS_POINTS <= len(focus_points) <= C.MAX_FOCUS_POINTS
    ):
        errors.append("focus_points 数量不合规")
    else:
        for point in focus_points:
            if not isinstance(point, str) or len(point) > C.MAX_FOCUS_POINT_CHARS:
                errors.append("focus_point 超长或非字符串")
        gold_refs = gold.get("focus_point_refs") or []
        if gold_refs and len(focus_points) != len(gold_refs):
            errors.append("focus_points 数量必须与 gold refs 一致，避免出处错位")
    if isinstance(summary, str):
        errors.extend(_fact_errors(summary, snapshot, seed, gold))
        candidate = {
            **gold,
            "summary": summary,
            "focus_points": focus_points if isinstance(focus_points, list) else [],
        }
        errors.extend(_summary_consistency(summary, candidate))
    if isinstance(focus_points, list):
        errors.extend(_fact_errors(" ".join(str(item) for item in focus_points), snapshot, seed, gold))
    reasons = narrative.get("semantic_reasons")
    if reasons is not None and not isinstance(reasons, dict):
        errors.append("semantic_reasons 必须是对象")
    elif isinstance(reasons, dict):
        errors.extend(
            _fact_errors(
                " ".join(str(value) for value in reasons.values()),
                snapshot,
                seed,
                gold,
            )
        )
    return errors


def assistant_content(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
