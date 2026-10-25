"""把真实合同整理成训练用 input_snapshot，并生成种子。

关键约束：
- 同一份合同、以及共享大量条款的“模板族”合同，只进入一个 split；
- 快照只保留被命中规则/语义规则引用的条款，并裁剪到 token 预算；
- 语义 gold 无法可靠标注时跳过，不默认生成 hit=true。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import config as C
from core.contract_parse import parse_contract, read_contract_text
from core.rule_engine import overall_risk, run_rules, semantic_rules

CATEGORY_MAP: dict[int, dict[str, Any]] = {
    1: {
        "name": "付款条款",
        "subcategory": "付款周期/比例/预付款/质保金",
        "rules": {"R-PAY-01", "R-PAY-02", "R-PRE-01", "R-WAR-01", "R-TAX-01"},
    },
    2: {
        "name": "履约保证金",
        "subcategory": "保证金比例与形式",
        "rules": {"R-DEP-01"},
    },
    3: {
        "name": "违约责任对等性",
        "subcategory": "违约责任是否单方加重",
        "rules": {"R-SEM-02"},
    },
    4: {
        "name": "法务条款",
        "subcategory": "印章/管辖/保密/知识产权/数据",
        "rules": {
            "R-SEAL-01", "R-SEAL-02", "R-JURIS-01",
            "R-CONF-01", "R-IP-01", "R-DATA-01",
        },
    },
    5: {
        "name": "业务条款",
        "subcategory": "背靠背/验收/续约/范围/计量/质量",
        "rules": {
            "R-BACK-01", "R-ACCEPT-01", "R-AUTO-01", "R-LIAB-01",
            "R-DEDUCT-01", "R-SCOPE-01", "R-DELIVERY-01",
            "R-MEASURE-01", "R-QUALITY-01", "R-SEM-01", "R-SEM-03",
        },
    },
    6: {
        "name": "资质与租赁方式",
        "subcategory": "主体资质/合同类型匹配/租赁计价明确性",
        "rules": {"R-SEM-04", "R-SEM-05"},
    },
    7: {
        "name": "霸王条款与风险分配",
        "subcategory": "单方加重/风险条款/提前退场赔偿",
        "rules": {"R-SEM-06", "R-SEM-07", "R-SEM-08"},
    },
    8: {
        "name": "保证金条款完整性",
        "subcategory": "投标保证金/履约保证金",
        "rules": {"R-SEM-09"},
    },
}

SEMANTIC_KEYWORDS = {
    "R-SEM-01": ("施工内容", "工程量", "成本分析", "成本测算"),
    "R-SEM-02": ("违约", "赔偿", "违约金"),
    "R-SEM-03": ("合同专用章", "项目部章", "公章", "盖章", "签署"),
    "R-SEM-04": ("资质", "许可证", "营业执照", "经营范围", "安全生产许可证"),
    "R-SEM-05": ("租赁", "租金", "台班", "按天", "按月", "按吨", "计价", "结算"),
    "R-SEM-06": ("单方", "随时解除", "概不负责", "一切损失", "最终解释权", "无条件", "不得异议"),
    "R-SEM-07": ("提前退场", "提前终止", "提前解除", "退场", "赔偿", "违约金"),
    "R-SEM-08": ("风险", "责任", "承担", "损失"),
    "R-SEM-09": ("投标保证金", "履约保证金", "保证金", "退还", "返还"),
}

SEMANTIC_CATEGORY = {
    "R-SEM-01": 5,
    "R-SEM-02": 3,
    "R-SEM-03": 5,
    "R-SEM-04": 6,
    "R-SEM-05": 6,
    "R-SEM-06": 7,
    "R-SEM-07": 7,
    "R-SEM-08": 7,
    "R-SEM-09": 8,
}
PARTY_PAIRS = (("甲方", "乙方"), ("出租方", "承租方"), ("发包方", "承包方"), ("买方", "卖方"))
POSITION_RE = re.compile(r"^(?P<clause_no>.+?)（第(?P<page>\d+)页）$")


def detect_contract_type(file_name: str) -> str:
    return "standard" if "标准" in file_name else "nonstandard"


def detect_is_scanned(file_name: str) -> bool:
    """输入已经是 MinerU/OCR 转好的 .md/.txt，解析成功时不再标记为扫描件。"""
    return False


def _position_parts(position: str) -> tuple[str, int] | None:
    match = POSITION_RE.match(position.strip())
    if not match:
        return None
    return match.group("clause_no"), int(match.group("page"))


def _clause_key(clause: dict[str, Any]) -> tuple[str, int]:
    return str(clause.get("clause_no")), int(clause.get("page", 1))


def select_clauses(
    parsed: dict[str, Any],
    hits: list[dict[str, Any]],
    semantic_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按“语义条款优先、命中证据次之”选条款，保证语义证据一定在快照里。"""
    all_clauses = parsed.get("clauses", [])
    order = {id(clause): index for index, clause in enumerate(all_clauses)}
    semantic_selected: list[dict[str, Any]] = []
    evidence_selected: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def add(target: list[dict[str, Any]], clause: dict[str, Any]) -> None:
        key = _clause_key(clause)
        if key in seen:
            return
        seen.add(key)
        target.append(clause)

    for candidate in semantic_candidates:
        keywords = SEMANTIC_KEYWORDS.get(candidate.get("rule_code", ""), ())
        for clause in all_clauses:
            text = clause.get("text", "")
            if any(keyword in text for keyword in keywords):
                add(semantic_selected, clause)

    for hit in hits:
        parts = _position_parts(str(hit.get("evidence_position", "")))
        if not parts:
            continue
        clause_no, page = parts
        for clause in all_clauses:
            if _clause_key(clause) == (clause_no, page):
                add(evidence_selected, clause)
                break

    selected = semantic_selected[: C.MAX_SNAPSHOT_CLAUSES]
    for clause in evidence_selected:
        if len(selected) >= C.MAX_SNAPSHOT_CLAUSES:
            break
        add(selected, clause)
    if not selected:
        selected = all_clauses[: C.MAX_SNAPSHOT_CLAUSES]
    selected.sort(key=lambda clause: order.get(id(clause), 0))
    return selected


def _trim_clause(clause: dict[str, Any]) -> dict[str, Any]:
    text = str(clause.get("text", "")).strip()
    if len(text) > C.MAX_CLAUSE_CHARS:
        text = text[: C.MAX_CLAUSE_CHARS].rstrip() + "…"
    return {
        "clause_no": clause.get("clause_no"),
        "title": clause.get("title"),
        "text": text,
        "page": clause.get("page", 1),
        "char_start": clause.get("char_start", 0),
    }


def compact_snapshot(
    parsed: dict[str, Any],
    hits: list[dict[str, Any]],
    semantic_candidates: list[dict[str, Any]],
    *,
    file_name: str,
) -> dict[str, Any]:
    basic = parsed.get("basic_info") or {}
    normalized = basic.get("normalized") or parsed.get("normalized") or {}
    candidates = semantic_candidates[: C.MAX_SEMANTIC_CANDIDATES]
    clauses = select_clauses(parsed, hits[: C.MAX_RULE_HITS], candidates)
    selected_positions = {
        f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）" for clause in clauses
    }
    selected_text = "\n".join(str(clause.get("text", "")) for clause in clauses)

    # 只保留证据条款确实进入快照的命中；否则模型无法从输入推导证据。
    compact_hits = []
    for hit in hits[: C.MAX_RULE_HITS]:
        position = str(hit.get("evidence_position", ""))
        if position in selected_positions or position in {"全文", "合同正文"}:
            compact_hits.append(
                {
                    "rule_code": hit["rule_code"],
                    "rule_name": hit["rule_name"],
                    "rule_category": hit["rule_category"],
                    "risk_level": hit["risk_level"],
                    "evidence_text": hit["evidence_text"],
                    "evidence_position": hit["evidence_position"],
                    "suggestion_text": hit["suggestion_text"],
                }
            )

    compact_semantics = []
    for rule in candidates:
        keywords = SEMANTIC_KEYWORDS.get(rule["rule_code"], ())
        if any(keyword in selected_text for keyword in keywords):
            compact_semantics.append(
                {
                    "rule_code": rule["rule_code"],
                    "rule_name": rule["rule_name"],
                    "semantic_query": rule.get("semantic_query") or rule["rule_name"],
                    "risk_level": rule["risk_level"],
                }
            )

    return {
        "contract_type": detect_contract_type(file_name),
        "is_scanned": detect_is_scanned(file_name),
        "parse_status": "success",
        "basic_info": {
            "contract_name": basic.get("contract_title") or Path(file_name).stem,
            "normalized": normalized,
        },
        "clauses": [_trim_clause(clause) for clause in clauses],
        "rule_hits": compact_hits,
        "semantic_candidates": compact_semantics,
    }


def build_reject_snapshot(
    reason: str,
    source_name: str | None = None,
    *,
    reject_type: str = "parse_failed",
    missing_fields: list[str] | None = None,
    clauses: list[dict[str, Any]] | None = None,
    semantic_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造拒答快照。

    老师原框架只有“解析失败”一种 reject；这里按 refusal 维度扩展为：
    parse_failed / insufficient / out_of_scope / semantic_unknown。
    """
    if reject_type not in C.REJECT_TYPES:
        raise ValueError(f"未知 reject_type: {reject_type}")
    status_map = {
        "parse_failed": "failed",
        "out_of_scope": "out_of_scope",
        "insufficient": "partial",
        "semantic_unknown": "partial",
    }
    return {
        "contract_type": "unknown",
        "is_scanned": reject_type == "parse_failed",
        "parse_status": status_map[reject_type],
        "reject_type": reject_type,
        "reject_reason": reason,
        "parse_error": reason,
        "missing_fields": missing_fields or [],
        "source_file": source_name,
        "basic_info": {"contract_name": source_name, "normalized": {}},
        "clauses": clauses or [],
        "rule_hits": [],
        "semantic_candidates": semantic_candidates or [],
    }


def _sentences(text: str) -> list[str]:
    return [
        sentence.strip(" \t#")
        for sentence in re.split(r"[。；;\n]+", text)
        if sentence.strip(" \t#")
    ]


def _first_sentence_with(text: str, keyword: str) -> str:
    matches = [sentence for sentence in _sentences(text) if keyword in sentence]
    substantive = [sentence for sentence in matches if len(sentence) >= 10]
    # 只有“五、违约责任”这类标题行时不作为证据；宁可不生成该语义样本。
    return substantive[0] if substantive else ""


def _party_ratio(text: str, party: str) -> float | None:
    match = re.search(party + r"[^。；\n]{0,24}?(\d+(?:\.\d+)?)\s*%", text)
    return float(match.group(1)) if match else None


def _label_breach_equality(text: str) -> dict[str, Any] | None:
    """判断违约责任是否对等；无法可靠判断时返回 None。"""
    ratio_a = ratio_b = None
    pair = next(
        ((a, b) for a, b in PARTY_PAIRS if a in text or b in text),
        ("甲方", "乙方"),
    )
    ratio_a = _party_ratio(text, pair[0])
    ratio_b = _party_ratio(text, pair[1])
    if ratio_a is not None and ratio_b is not None:
        hit = abs(ratio_a - ratio_b) > 1e-9
        reason = (
            f"{pair[0]}违约责任为 {ratio_a:g}%，{pair[1]}为 {ratio_b:g}%，责任比例不对等。"
            if hit
            else f"{pair[0]}与{pair[1]}违约责任比例均为 {ratio_a:g}%，未发现明显不对等。"
        )
        return {"hit": hit, "reason": reason}
    if ratio_a is not None or ratio_b is not None:
        return {
            "hit": True,
            "reason": "只有一方约定了可量化的违约责任，另一方没有对应约定，责任不对等。",
        }

    equality_markers = (
        "双方违约责任对等", "违约责任对等", "责任对等", "各自承担",
        "均承担", "同等承担", "按同一比例", "同比例承担", "各自按",
    )
    single_markers = (
        "仅乙方", "仅甲方", "由乙方承担全部", "由甲方承担全部",
        "乙方单方承担", "甲方单方承担", "甲方不承担", "乙方不承担",
    )
    if any(marker in text for marker in equality_markers):
        return {"hit": False, "reason": "条款明确约定双方违约责任对等，未发现单方加重。"}
    if any(marker in text for marker in single_markers):
        return {"hit": True, "reason": "条款存在单方承担违约责任的表述，责任不对等。"}
    if pair[0] in text and pair[1] in text:
        return {
            "hit": False,
            "reason": "条款同时约定了双方责任，未发现明确的单方加重表述。",
        }
    # 既没有比例、也没有当事人或对等/单方表述，无法可靠标注。
    return None


def label_semantic(
    parsed: dict[str, Any],
    rule: dict[str, Any],
) -> dict[str, Any] | None:
    """给语义规则生成可校验的 gold；无法可靠标注时返回 None。"""
    rule_code = rule["rule_code"]
    clauses = parsed.get("clauses", [])
    full_text = parsed.get("full_text", "")

    if rule_code == "R-SEM-02":
        breach = [clause for clause in clauses if "违约" in clause.get("title", "")]
        if not breach:
            breach = [clause for clause in clauses if "违约" in clause.get("text", "")]
        if not breach:
            return None
        clause = breach[0]
        text = clause.get("text", "")
        label = _label_breach_equality(text)
        if label is None:
            return None
        quote = _first_sentence_with(text, "违约") or _first_sentence_with(text, "责任")
        if not quote:
            return None
        return {
            "rule_code": rule_code,
            "hit": label["hit"],
            "risk_level": rule["risk_level"],
            "reason": label["reason"],
            "evidence_position": f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）",
            "evidence_quote": quote,
        }

    if rule_code == "R-SEM-03":
        keyword = next(
            (item for item in ("合同专用章", "项目部章", "公章", "盖章") if item in full_text),
            None,
        )
        if keyword is None:
            return None
        signing_clauses = [
            clause
            for clause in clauses
            if keyword in clause.get("text", "")
            and any(marker in f"{clause.get('title', '')}{clause.get('text', '')}" for marker in ("签", "盖章", "用印"))
        ]
        clause = signing_clauses[0] if signing_clauses else next(
            (item for item in clauses if keyword in item.get("text", "")),
            None,
        )
        if clause is None:
            return None
        quote = _first_sentence_with(clause.get("text", ""), keyword)
        if not quote:
            return None
        hit = keyword in {"合同专用章", "项目部章"}
        return {
            "rule_code": rule_code,
            "hit": hit,
            "risk_level": rule["risk_level"],
            "reason": (
                "合同使用合同专用章/项目部章签署，需确认是否取得有效授权。"
                if hit
                else "合同使用公章或普通盖章方式签署，未发现特殊印章风险。"
            ),
            "evidence_position": f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）",
            "evidence_quote": quote,
        }

    if rule_code == "R-SEM-04":
        if not any(marker in full_text for marker in ("施工", "分包", "工程", "承包")):
            return None
        candidates = [
            clause for clause in clauses
            if any(keyword in clause.get("text", "") for keyword in SEMANTIC_KEYWORDS[rule_code])
        ]
        if not candidates:
            return None
        negative = ("无资质", "未取得", "不具备", "超越资质", "资质过期", "个人承包", "个人名义")
        positive = ("具备", "持有", "已取得", "资质证书", "安全生产许可证")
        negative_clauses = [
            item for item in candidates
            if any(marker in item.get("text", "") for marker in negative)
        ]
        positive_clauses = [
            item for item in candidates
            if any(marker in item.get("text", "") for marker in positive)
        ]
        if negative_clauses:
            clause = negative_clauses[0]
            hit, reason = True, "合同中出现无资质、未取得或超越资质范围的表述，主体资质存疑。"
        elif positive_clauses:
            clause = positive_clauses[0]
            hit, reason = False, "合同约定了主体应具备的资质或许可证要求。"
        else:
            return None
        quote = _first_sentence_with(clause.get("text", ""), "资质") or _first_sentence_with(
            clause.get("text", ""), "许可证"
        )
        if not quote:
            return None
        return {
            "rule_code": rule_code,
            "hit": hit,
            "risk_level": rule["risk_level"],
            "reason": reason,
            "evidence_position": f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）",
            "evidence_quote": quote,
        }

    if rule_code == "R-SEM-05":
        if "租赁" not in full_text:
            return None
        lease_clauses = [
            clause for clause in clauses
            if "租赁" in clause.get("text", "") or "租金" in clause.get("text", "")
        ]
        if not lease_clauses:
            return None
        all_lease_text = "\n".join(clause.get("text", "") for clause in lease_clauses)
        explicit = (
            "按天", "按月", "按日", "按吨", "按台班", "按平方米", "按立方米",
            "计量", "计价", "租金标准", "元/吨", "元/天", "元/月",
        )
        if any(marker in all_lease_text for marker in explicit):
            clause = next(
                (item for item in lease_clauses if any(marker in item.get("text", "") for marker in explicit)),
                lease_clauses[0],
            )
            hit, reason = False, "租赁合同已约定租赁方式、计价单位或租金标准。"
        else:
            clause = lease_clauses[0]
            hit, reason = True, "租赁合同未明确租赁方式、计价单位或租金标准。"
        quote = _first_sentence_with(clause.get("text", ""), "租金") or _first_sentence_with(
            clause.get("text", ""), "租赁"
        )
        if not quote:
            return None
        return {
            "rule_code": rule_code,
            "hit": hit,
            "risk_level": rule["risk_level"],
            "reason": reason,
            "evidence_position": f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）",
            "evidence_quote": quote,
        }

    if rule_code == "R-SEM-06":
        unfair = (
            "概不负责", "一切损失由乙方承担", "全部损失由乙方承担",
            "最终解释权", "甲方有权随时解除", "甲方有权单方解除",
            "乙方不得提出异议", "乙方不得异议", "无条件接受",
            "甲方不承担任何责任", "无论何种原因均由乙方承担",
        )
        balanced = ("双方协商一致", "对等", "同等责任", "各自承担", "按责任比例")
        candidates = [
            clause for clause in clauses
            if any(marker in clause.get("text", "") for marker in unfair)
        ]
        if candidates:
            clause = candidates[0]
            hit, reason = True, "合同存在单方免责、单方解除或最终解释权等霸王条款。"
        else:
            clause = next(
                (
                    item for item in clauses
                    if any(marker in item.get("text", "") for marker in balanced)
                    and any(keyword in item.get("text", "") for keyword in ("责任", "解除", "赔偿"))
                ),
                None,
            )
            if clause is None:
                return None
            hit, reason = False, "合同约定了双方协商、对等责任或按责任比例承担。"
        quote = _first_sentence_with(clause.get("text", ""), "责任") or _first_sentence_with(
            clause.get("text", ""), "解除"
        )
        if not quote:
            return None
        return {
            "rule_code": rule_code,
            "hit": hit,
            "risk_level": rule["risk_level"],
            "reason": reason,
            "evidence_position": f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）",
            "evidence_quote": quote,
        }

    if rule_code == "R-SEM-07":
        triggers = ("提前退场", "提前终止", "提前解除", "提前撤场")
        if not any(trigger in full_text for trigger in triggers):
            return None
        candidates = [
            clause for clause in clauses
            if any(trigger in clause.get("text", "") for trigger in triggers)
        ]
        if not candidates:
            return None
        single = (
            "乙方承担", "不予退还", "无权要求", "甲方不承担",
            "乙方自行承担", "全部由乙方",
        )
        mutual = ("双方", "各自", "对等", "按实际损失")
        single_clauses = [
            item for item in candidates
            if any(marker in item.get("text", "") for marker in single)
        ]
        mutual_clauses = [
            item for item in candidates
            if any(marker in item.get("text", "") for marker in mutual)
        ]
        if single_clauses:
            clause = single_clauses[0]
            hit, reason = True, "提前退场/终止时赔偿或违约金由一方单方承担。"
        elif mutual_clauses:
            clause = mutual_clauses[0]
            hit, reason = False, "提前退场/终止的赔偿按双方约定或实际损失处理。"
        else:
            return None
        quote = _first_sentence_with(clause.get("text", ""), "提前") or _first_sentence_with(
            clause.get("text", ""), "赔偿"
        )
        if not quote:
            return None
        return {
            "rule_code": rule_code,
            "hit": hit,
            "risk_level": rule["risk_level"],
            "reason": reason,
            "evidence_position": f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）",
            "evidence_quote": quote,
        }

    if rule_code == "R-SEM-08":
        one_sided = (
            "所有风险由乙方承担", "一切风险由乙方", "全部风险由乙方",
            "所有损失由乙方承担", "一切损失由乙方承担",
            "甲方不承担任何风险", "甲方不承担任何责任",
        )
        shared = ("双方共担", "风险共担", "按责任划分", "各自承担", "按责任比例")
        candidates = [
            clause for clause in clauses
            if any(marker in clause.get("text", "") for marker in one_sided)
        ]
        if candidates:
            clause = candidates[0]
            hit, reason = True, "合同将全部风险或损失单方分配给一方承担。"
        else:
            clause = next(
                (item for item in clauses if any(marker in item.get("text", "") for marker in shared)),
                None,
            )
            if clause is None:
                return None
            hit, reason = False, "合同按责任划分或双方共担风险。"
        quote = _first_sentence_with(clause.get("text", ""), "风险") or _first_sentence_with(
            clause.get("text", ""), "损失"
        )
        if not quote:
            return None
        return {
            "rule_code": rule_code,
            "hit": hit,
            "risk_level": rule["risk_level"],
            "reason": reason,
            "evidence_position": f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）",
            "evidence_quote": quote,
        }

    if rule_code == "R-SEM-09":
        if "保证金" not in full_text:
            return None
        candidates = [
            clause for clause in clauses if "保证金" in clause.get("text", "")
        ]
        if not candidates:
            return None
        all_text = "\n".join(clause.get("text", "") for clause in candidates)
        # 只看含“保证金”的句子，避免把同一条款里的“付款比例”误当成保证金比例。
        guarantee_sentences = [
            sentence for sentence in _sentences(all_text) if "保证金" in sentence
        ]
        guarantee_text = "\n".join(guarantee_sentences)
        complete_markers = ("退还", "返还", "期限", "比例", "抵扣", "扣除")
        if any(marker in guarantee_text for marker in complete_markers):
            clause = next(
                (
                    item for item in candidates
                    if any(
                        marker in "\n".join(
                            sentence for sentence in _sentences(item.get("text", ""))
                            if "保证金" in sentence
                        )
                        for marker in complete_markers
                    )
                ),
                candidates[0],
            )
            hit, reason = False, "保证金条款包含比例、退还、期限或抵扣等关键要素。"
        else:
            clause = candidates[0]
            hit, reason = True, "保证金条款缺少比例、退还条件、期限或抵扣方式。"
        quote = _first_sentence_with(
            "\n".join(
                sentence for sentence in _sentences(clause.get("text", ""))
                if "保证金" in sentence
            ) or clause.get("text", ""),
            "保证金",
        )
        if not quote:
            return None
        return {
            "rule_code": rule_code,
            "hit": hit,
            "risk_level": rule["risk_level"],
            "reason": reason,
            "evidence_position": f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）",
            "evidence_quote": quote,
        }

    # R-SEM-01 需要成本分析表，当前数据无法可靠标注，跳过。
    return None


def _seed_slug(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _base_seed(
    *,
    seed_id: str,
    split: str,
    category_id: int,
    subcategory: str,
    scenario: str,
    user_goal: str,
    snapshot: dict[str, Any],
    required_facts: list[str],
    expected_output_points: list[str],
    expected_semantic: list[dict[str, Any]],
    output_required: bool,
    sample_type: str,
) -> dict[str, Any]:
    return {
        "seed_id": seed_id,
        "split": split,
        "category_id": category_id,
        "category": CATEGORY_MAP[category_id]["name"],
        "subcategory": subcategory,
        "scenario": scenario,
        "user_goal": user_goal,
        "contract_type": snapshot.get("contract_type", "unknown"),
        "is_scanned": snapshot.get("is_scanned", False),
        "input_snapshot": snapshot,
        "required_facts": required_facts,
        "expected_output_points": expected_output_points,
        "expected_semantic": expected_semantic,
        "required_questions": [],
        "prohibited_actions": list(C.STANDARD_RED_LINES),
        "output_required": output_required,
        "sample_type": sample_type,
    }


def _review_points(hits: list[dict[str, Any]], risk: str) -> list[str]:
    codes = [hit["rule_code"] for hit in hits]
    names = [hit["rule_name"] for hit in hits[:5]]
    points = [f"整体风险等级必须为 {risk}"]
    if codes:
        points.append("hit_rules 必须且只能覆盖：" + "、".join(codes))
        points.append("关注点需覆盖：" + "、".join(names))
    else:
        points.append("没有 rule_hits 时说明未命中确定性规则，并给出补充复核建议")
    return points


def _align_labels(
    snapshot: dict[str, Any],
    labels: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    codes = {item["rule_code"] for item in snapshot.get("semantic_candidates", [])}
    return [labels[code] for code in codes if code in labels]


def build_review_seed(
    parsed: dict[str, Any],
    hits: list[dict[str, Any]],
    semantic_candidates: list[dict[str, Any]],
    semantic_labels: dict[str, dict[str, Any]],
    *,
    split: str,
    category_id: int,
    file_name: str,
    contract_key: str,
    sequence: int,
) -> dict[str, Any] | None:
    snapshot = compact_snapshot(parsed, hits, semantic_candidates, file_name=file_name)
    expected_labels = _align_labels(snapshot, semantic_labels)
    snapshot_hits = snapshot["rule_hits"]
    if not snapshot_hits and not snapshot["semantic_candidates"]:
        return None
    risk = overall_risk(snapshot_hits + [label for label in expected_labels if label["hit"]])
    return _base_seed(
        seed_id=f"cat{category_id}_review_{contract_key}_{sequence:03d}",
        split=split,
        category_id=category_id,
        subcategory=CATEGORY_MAP[category_id]["subcategory"],
        scenario=f"审查《{snapshot['basic_info']['contract_name']}》的{CATEGORY_MAP[category_id]['name']}",
        user_goal="基于规则引擎命中结果生成结构化审查意见",
        snapshot=snapshot,
        required_facts=[hit["rule_code"] for hit in snapshot_hits],
        expected_output_points=_review_points(snapshot_hits, risk),
        expected_semantic=expected_labels,
        output_required=True,
        sample_type="review",
    )


def build_full_review_seed(
    parsed: dict[str, Any],
    hits: list[dict[str, Any]],
    semantic_candidates: list[dict[str, Any]],
    semantic_labels: dict[str, dict[str, Any]],
    *,
    split: str,
    file_name: str,
    contract_key: str,
    sequence: int,
) -> dict[str, Any] | None:
    snapshot = compact_snapshot(parsed, hits, semantic_candidates, file_name=file_name)
    expected_labels = _align_labels(snapshot, semantic_labels)
    snapshot_hits = snapshot["rule_hits"]
    if not snapshot_hits and not snapshot["semantic_candidates"]:
        return None
    risk = overall_risk(snapshot_hits + [label for label in expected_labels if label["hit"]])
    return _base_seed(
        seed_id=f"cat5_full_review_{contract_key}_{sequence:03d}",
        split=split,
        category_id=5,
        subcategory="全量命中综合审查",
        scenario=f"对《{snapshot['basic_info']['contract_name']}》做整份合同审查",
        user_goal="汇总全部确定性规则命中并给出整体风险",
        snapshot=snapshot,
        required_facts=[hit["rule_code"] for hit in snapshot_hits],
        expected_output_points=_review_points(snapshot_hits, risk),
        expected_semantic=expected_labels,
        output_required=True,
        sample_type="review",
    )


def build_semantic_seed(
    parsed: dict[str, Any],
    rule: dict[str, Any],
    label: dict[str, Any],
    *,
    split: str,
    category_id: int,
    file_name: str,
    contract_key: str,
    sequence: int,
) -> dict[str, Any] | None:
    snapshot = compact_snapshot(parsed, [], [rule], file_name=file_name)
    if not snapshot["semantic_candidates"]:
        return None
    return _base_seed(
        seed_id=(
            f"cat{category_id}_semantic_{rule['rule_code'].lower().replace('-', '')}"
            f"_{contract_key}_{sequence:03d}"
        ),
        split=split,
        category_id=category_id,
        subcategory=CATEGORY_MAP[category_id]["subcategory"],
        scenario=f"判断《{snapshot['basic_info']['contract_name']}》是否命中 {rule['rule_name']}",
        user_goal="只输出语义规则判断，不生成完整报告",
        snapshot=snapshot,
        required_facts=[rule["rule_code"]],
        expected_output_points=[
            f"必须判断 {rule['rule_code']}",
            f"expected hit={label['hit']}",
            "必须引用条款原文作为证据",
        ],
        expected_semantic=[label],
        output_required=True,
        sample_type="semantic",
    )


REJECT_POINTS = {
    "parse_failed": [
        "必须输出 status=rejected、reject_type=parse_failed",
        "必须说明 OCR/解析失败的原因",
        "必须列出需要补充的材料",
        "不得输出 hit_rules / summary / focus_points / focus_point_refs",
    ],
    "insufficient": [
        "必须输出 status=rejected、reject_type=insufficient",
        "必须指出缺少哪些关键字段或条款",
        "不得在字段缺失时强行给出风险等级",
        "不得编造未提供的金额/比例/天数",
    ],
    "out_of_scope": [
        "必须输出 status=rejected、reject_type=out_of_scope",
        "必须说明该合同不属于本次审查范围",
        "不得输出审查报告或规则命中",
    ],
    "semantic_unknown": [
        "必须输出 status=rejected、reject_type=semantic_unknown",
        "必须说明语义规则无法判断的原因",
        "必须指出需要补充的支撑材料",
        "不得编造语义规则的 hit=true/false",
    ],
}


def build_reject_seed(
    split: str,
    reason: str,
    sequence: int,
    source_name: str,
    *,
    reject_type: str = "parse_failed",
    missing_fields: list[str] | None = None,
    clauses: list[dict[str, Any]] | None = None,
    semantic_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if reject_type not in C.REJECT_TYPES:
        raise ValueError(f"未知 reject_type: {reject_type}")
    snapshot = build_reject_snapshot(
        reason,
        source_name,
        reject_type=reject_type,
        missing_fields=missing_fields,
        clauses=clauses,
        semantic_candidates=semantic_candidates,
    )
    scenarios = {
        "parse_failed": f"{source_name} OCR 失败或未识别到任何条款",
        "insufficient": f"{source_name} 关键字段/条款不完整，无法完成规则审查",
        "out_of_scope": f"{source_name} 不属于工程/租赁/分包合同审查范围",
        "semantic_unknown": f"{source_name} 缺少语义规则所需的支撑材料",
    }
    return _base_seed(
        seed_id=f"reject_{reject_type}_{split}_{_seed_slug(source_name)}_{sequence:03d}",
        split=split,
        category_id=4,
        subcategory="拒答与边界",
        scenario=scenarios[reject_type],
        user_goal="拒绝强行输出报告，并说明原因与需要补充的材料",
        snapshot=snapshot,
        required_facts=[],
        expected_output_points=REJECT_POINTS[reject_type],
        expected_semantic=[],
        output_required=False,
        sample_type="reject",
    )


VARIANT_RE = re.compile(C.VARIANT_FILE_PATTERN)


def variant_parent_name(file_name: str) -> str | None:
    """从 {stem}__v01.md 还原父合同文件名。"""
    match = VARIANT_RE.match(file_name)
    if not match:
        return None
    return f"{match.group('stem')}.{match.group('ext')}"


def build_variant_parent_map(
    raw_paths: list[Path],
    variant_paths: list[Path],
) -> dict[Path, Path]:
    """建立 变体路径 -> 父合同路径 的映射；找不到父合同时直接报错。"""
    by_name = {path.name: path for path in raw_paths}
    mapping: dict[Path, Path] = {}
    for variant_path in variant_paths:
        parent_name = variant_parent_name(variant_path.name)
        if parent_name is None:
            raise ValueError(f"变体文件名不符合 {{stem}}__vNN.md 约定: {variant_path.name}")
        parent = by_name.get(parent_name)
        if parent is None:
            raise ValueError(f"变体 {variant_path.name} 找不到父合同 {parent_name}")
        mapping[variant_path] = parent
    return mapping


def _clause_hashes(parsed: dict[str, Any]) -> set[str]:
    hashes: set[str] = set()
    for clause in parsed.get("clauses", []):
        normalized = re.sub(r"\s+", "", str(clause.get("text", "")))
        if len(normalized) >= 20:
            hashes.add(hashlib.sha1(normalized.encode("utf-8")).hexdigest())
    return hashes


def _cluster_contracts(
    paths: list[Path],
    parsed_map: dict[Path, dict[str, Any]],
    variant_parent_map: dict[Path, Path] | None = None,
) -> list[list[Path]]:
    """把共享条款的模板合同聚成一族（任一 >=20 字条款相同即同族）。

    变体先强制并到父合同，再走条款 hash 聚类；这样即使变体改得与原合同
    不再共享条款，也不会被分到别的 split。
    """
    parent = {path: path for path in paths}

    def find(path: Path) -> Path:
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    def union(left: Path, right: Path) -> None:
        parent[find(left)] = find(right)

    if variant_parent_map:
        for variant, original in variant_parent_map.items():
            if variant in parent and original in parent:
                union(variant, original)

    hashes = {path: _clause_hashes(parsed_map[path]) for path in paths}
    for index, left in enumerate(paths):
        for right in paths[:index]:
            # 只要共享一条 >=20 字的条款，就视为同一模板族，整族放进同一个 split。
            # 这比 Jaccard 阈值更严格，能从根上避免 final_test 出现 train 原句。
            if hashes[left] & hashes[right]:
                union(left, right)

    grouped: dict[Path, list[Path]] = defaultdict(list)
    for path in paths:
        grouped[find(path)].append(path)
    clusters = [sorted(cluster) for cluster in grouped.values()]
    clusters.sort(key=lambda cluster: hashlib.sha1(cluster[0].name.encode("utf-8")).hexdigest())
    return clusters


def assign_contract_splits(
    paths: list[Path],
    parsed_map: dict[Path, dict[str, Any]] | None = None,
    variant_parent_map: dict[Path, Path] | None = None,
) -> dict[Path, str]:
    """按合同/模板族切分，保证同源条款不会同时出现在两个 split。"""
    ordered = sorted(paths)
    total = len(ordered)
    if total == 0:
        return {}
    if total < 3:
        return {path: "train" for path in ordered}

    validation_count = max(1, round(total * C.VALIDATION_CONTRACT_RATIO))
    final_test_count = max(1, round(total * C.FINAL_TEST_CONTRACT_RATIO))
    if validation_count + final_test_count >= total:
        validation_count = final_test_count = 0
    capacities = {
        "train": total - validation_count - final_test_count,
        "validation": validation_count,
        "final_test": final_test_count,
    }
    clusters = (
        _cluster_contracts(ordered, parsed_map, variant_parent_map)
        if parsed_map is not None
        else [[path] for path in sorted(ordered, key=lambda item: hashlib.sha1(item.name.encode("utf-8")).hexdigest())]
    )

    result: dict[Path, str] = {}
    for cluster in clusters:
        size = len(cluster)
        fitting = [split for split in ("validation", "final_test", "train") if capacities[split] >= size]
        split = min(fitting, key=lambda item: capacities[item] - size) if fitting else max(capacities, key=capacities.get)
        for path in cluster:
            result[path] = split
        capacities[split] -= size

    # 尽量保证 validation/final_test 各至少有一个模板族。
    for target in ("validation", "final_test"):
        if any(value == target for value in result.values()):
            continue
        train_clusters = [cluster for cluster in clusters if result[cluster[0]] == "train"]
        train_clusters.sort(key=len)
        for cluster in train_clusters:
            if len(train_clusters) > 1:
                for path in cluster:
                    result[path] = target
                capacities["train"] += len(cluster)
                capacities[target] -= len(cluster)
                break
    return result


def cross_split_clause_overlap(
    split_map: dict[Path, str],
    parsed_map: dict[Path, dict[str, Any]],
) -> list[tuple[str, str, int]]:
    """返回跨 split 的相同条款数量，用于 00 的隔离检查。"""
    hashes_by_split: dict[str, set[str]] = {split: set() for split in C.SPLITS}
    for path, split in split_map.items():
        hashes_by_split[split].update(_clause_hashes(parsed_map[path]))
    overlaps = []
    for left, right in (("train", "validation"), ("train", "final_test"), ("validation", "final_test")):
        count = len(hashes_by_split[left] & hashes_by_split[right])
        if count:
            overlaps.append((left, right, count))
    return overlaps


def build_seeds_from_contracts(
    paths: list[Path],
    rules: list[dict[str, Any]],
    variant_parent_map: dict[Path, Path] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[Path, str], dict[Path, dict[str, Any]]]:
    """从真实合同（含变体）生成种子池，返回 (pools, split_map, parsed_map)。"""
    parsed_map = {
        path: parse_contract(read_contract_text(path), path.name)
        for path in paths
    }
    split_map = assign_contract_splits(paths, parsed_map, variant_parent_map)
    semantic_rule_map = {rule["rule_code"]: rule for rule in semantic_rules(rules)}
    pools: dict[str, list[dict[str, Any]]] = {split: [] for split in C.SPLITS}
    contracts_by_split = {
        split: [path for path in sorted(paths) if split_map[path] == split]
        for split in C.SPLITS
    }

    for path in sorted(paths):
        split = split_map[path]
        parsed = parsed_map[path]
        text = read_contract_text(path)
        contract_key = _seed_slug(text)
        hits = run_rules(
            parsed,
            rules,
            is_standard_version=detect_contract_type(path.name) == "standard",
        )

        if not parsed.get("clauses"):
            source = path.name
            pools[split].append(
                build_reject_seed(
                    split,
                    f"{source} 未识别到任何条款，无法进行规则审查",
                    1,
                    source,
                )
            )
            continue

        candidate_rules = [
            rule for rule in semantic_rules(rules)
            if rule["rule_code"] in SEMANTIC_KEYWORDS
        ]
        labels: dict[str, dict[str, Any]] = {}
        for rule in candidate_rules:
            label = label_semantic(parsed, rule)
            if label:
                labels[rule["rule_code"]] = label
        labeled_semantics = [semantic_rule_map[code] for code in labels if code in semantic_rule_map]

        category_seq: dict[int, int] = defaultdict(int)
        for category_id, meta in CATEGORY_MAP.items():
            category_hits = [hit for hit in hits if hit["rule_code"] in meta["rules"]]
            category_semantics = [item for item in labeled_semantics if item["rule_code"] in meta["rules"]]
            if not category_hits and not category_semantics:
                continue
            if not category_hits:
                continue
            category_seq[category_id] += 1
            seed = build_review_seed(
                parsed,
                category_hits,
                category_semantics,
                labels,
                split=split,
                category_id=category_id,
                file_name=path.name,
                contract_key=contract_key,
                sequence=category_seq[category_id],
            )
            if seed:
                pools[split].append(seed)

        if C.INCLUDE_FULL_REVIEW_SEED and hits:
            seed = build_full_review_seed(
                parsed,
                hits,
                labeled_semantics,
                labels,
                split=split,
                file_name=path.name,
                contract_key=contract_key,
                sequence=1,
            )
            if seed:
                pools[split].append(seed)

        semantic_seq: dict[str, int] = defaultdict(int)
        for rule_code, label in labels.items():
            rule = semantic_rule_map[rule_code]
            category_id = SEMANTIC_CATEGORY.get(rule_code, 5)
            semantic_seq[rule_code] += 1
            seed = build_semantic_seed(
                parsed,
                rule,
                label,
                split=split,
                category_id=category_id,
                file_name=path.name,
                contract_key=contract_key,
                sequence=semantic_seq[rule_code],
            )
            if seed:
                pools[split].append(seed)

    if C.INCLUDE_REJECT_SEEDS:
        for split in C.SPLITS:
            split_paths = contracts_by_split[split]
            for reject_type in C.REJECT_TYPES:
                for index in range(C.REJECT_SEEDS_PER_SPLIT):
                    # 同一种拒答生成多条时，换不同父合同，避免完全重复样本。
                    source_path = (
                        split_paths[index % len(split_paths)] if split_paths else None
                    )
                    source_name = source_path.name if source_path else f"{split}_contract"
                    source_parsed = parsed_map.get(source_path) if source_path else None
                    # insufficient / semantic_unknown 需要真实条款做上下文；
                    # 没有合同时只保留 parse_failed / out_of_scope。
                    if (
                        reject_type in {"insufficient", "semantic_unknown"}
                        and not (source_parsed and source_parsed.get("clauses"))
                    ):
                        continue
                    clauses: list[dict[str, Any]] = []
                    semantic_candidates: list[dict[str, Any]] = []
                    missing_fields: list[str] = []
                    type_source_name = source_name
                    if reject_type == "parse_failed":
                        reason = f"{type_source_name} OCR 未识别到有效条款，无法进行规则审查"
                    elif reject_type == "out_of_scope":
                        type_source_name = f"{split}_非审查范围合同{index + 1}"
                        reason = f"{type_source_name} 不属于工程/租赁/分包合同审查范围"
                    elif reject_type == "insufficient":
                        if source_parsed and source_parsed.get("clauses"):
                            clauses = [
                                _trim_clause(clause)
                                for clause in source_parsed["clauses"][:1]
                            ]
                        missing_fields = [
                            "contract_amount", "payment_days",
                            "payment_ratio", "prepay_ratio",
                        ]
                        reason = (
                            f"{type_source_name} 缺少金额/付款/结算等关键字段，"
                            "规则引擎无法完成阈值判断"
                        )
                    else:  # semantic_unknown
                        if source_parsed and source_parsed.get("clauses"):
                            candidates = [
                                clause for clause in source_parsed["clauses"]
                                if any(
                                    keyword in clause.get("text", "")
                                    for keyword in ("施工内容", "工程量", "成本分析", "成本测算")
                                )
                            ]
                            clauses = [
                                _trim_clause(clause)
                                for clause in (candidates[:1] or source_parsed["clauses"][:1])
                            ]
                        semantic_rule = semantic_rule_map.get("R-SEM-01")
                        if semantic_rule:
                            semantic_candidates = [
                                {
                                    "rule_code": semantic_rule["rule_code"],
                                    "rule_name": semantic_rule["rule_name"],
                                    "semantic_query": (
                                        semantic_rule.get("semantic_query")
                                        or semantic_rule["rule_name"]
                                    ),
                                    "risk_level": semantic_rule["risk_level"],
                                }
                            ]
                        reason = (
                            "缺少成本分析表/测算表，无法判断施工内容、工程量清单"
                            "与成本分析是否一致"
                        )
                    pools[split].append(
                        build_reject_seed(
                            split,
                            reason,
                            index + 1,
                            type_source_name,
                            reject_type=reject_type,
                            missing_fields=missing_fields,
                            clauses=clauses,
                            semantic_candidates=semantic_candidates,
                        )
                    )

    return pools, split_map, parsed_map
