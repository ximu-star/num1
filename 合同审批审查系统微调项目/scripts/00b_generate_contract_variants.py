"""用 DeepSeek 对真实合同做“可控变体”，供 00_build_seeds.py 扩充训练种子。

原则：
- DeepSeek 只改一处文本，rule_hits / semantic labels 全部由规则引擎重新计算；
- 每个变体必须仍能被 parse_contract 切出条款，且未改动的条款基本保留；
- 变体文件命名 {父stem}__vNN.md，00 会强制变体跟随父合同同一个 split。

运行：
    python scripts/00b_generate_contract_variants.py --dry-run
    python scripts/00b_generate_contract_variants.py --resume
    python scripts/00_build_seeds.py
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from core.contract_parse import extract_normalized, parse_contract, read_contract_text  # noqa: E402
from core.deepseek import DeepSeekError, get_client  # noqa: E402
from core.io_utils import read_jsonl, write_jsonl  # noqa: E402
from core.prompts import variant_prompt  # noqa: E402
from core.rule_engine import load_rules, run_rules, semantic_rules  # noqa: E402
from core.snapshot import (  # noqa: E402
    SEMANTIC_KEYWORDS,
    _clause_hashes,
    assign_contract_splits,
    detect_contract_type,
    label_semantic,
)

# 维度 -> 现有阈值规则。变体必须让恰好这一条规则翻转。
THRESHOLD_DIMENSIONS = {
    "payment_days": "R-PAY-01",
    "payment_ratio": "R-PAY-02",
    "prepay_ratio": "R-PRE-01",
    "performance_bond": "R-DEP-01",
    "warranty_ratio": "R-WAR-01",
    "tax_rate": "R-TAX-01",
}

# 文本/语义型维度 -> 项目本地语义规则。
TEXT_DIMENSIONS = {
    "qualification": "R-SEM-04",
    "lease_method": "R-SEM-05",
    "unfair_clause": "R-SEM-06",
    "early_exit": "R-SEM-07",
    "risk_clause": "R-SEM-08",
}

# 每个阈值维度的安全值/风险值（safe = 不命中，risk = 命中）。
TARGET_VALUES: dict[str, dict[str, float]] = {
    "payment_days": {"safe": 30, "risk": 90},
    "payment_ratio": {"safe": 0.8, "risk": 0.3},
    "prepay_ratio": {"safe": 0.15, "risk": 0.5},
    "performance_bond": {"safe": 0.08, "risk": 0.15},
    "warranty_ratio": {"safe": 0.02, "risk": 0.05},
    "tax_rate": {"safe": 13, "risk": 6},
}
BID_BOND_VALUES = {"safe": 0.02, "risk": 0.15}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成合同可控变体（DeepSeek）")
    parser.add_argument("--raw-dir", type=Path, default=C.RAW_CONTRACTS_DIR)
    parser.add_argument("--variant-dir", type=Path, default=C.VARIANT_CONTRACTS_DIR)
    parser.add_argument("--variants-per-contract", type=int, default=C.VARIANTS_PER_CONTRACT)
    parser.add_argument("--dimensions", type=str, default=None, help="逗号分隔，默认全部")
    parser.add_argument("--max-contracts", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-skipped",
        action="store_true",
        help="配合 --resume：只重试 manifest 中 status=skipped 的条目，不动已成功的变体",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与 prompt 长度，不调 API")
    return parser.parse_args()


def load_base_paths(raw_dir: Path) -> list[Path]:
    paths = sorted(
        path for pattern in ("*.md", "*.txt")
        for path in raw_dir.glob(pattern)
    )
    if not paths:
        raise FileNotFoundError(f"没有找到合同文件: {raw_dir}")
    return paths


def parse_all(paths: list[Path]) -> dict[Path, dict[str, Any]]:
    return {
        path: parse_contract(read_contract_text(path), path.name)
        for path in paths
    }


def semantic_profile(parsed: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for rule in semantic_rules(rules):
        if rule["rule_code"] not in SEMANTIC_KEYWORDS:
            continue
        label = label_semantic(parsed, rule)
        if label:
            labels[rule["rule_code"]] = label
    return labels


def rule_hit_codes(parsed: dict[str, Any], rules: list[dict[str, Any]], file_name: str) -> set[str]:
    hits = run_rules(parsed, rules, is_standard_version=detect_contract_type(file_name) == "standard")
    return {hit["rule_code"] for hit in hits}


def _format_percent(value: float) -> str:
    return f"{value * 100:g}%"


def _target_for_threshold(dimension: str, current_hit: bool) -> float:
    return TARGET_VALUES[dimension]["risk" if not current_hit else "safe"]


def _threshold_instruction(
    dimension: str,
    rule: dict[str, Any],
    target_value: float,
    target_hit: bool,
) -> str:
    if dimension == "payment_days":
        return (
            f"把合同中“验收后/结算后/交付后 X 日内支付”的期限改为 {target_value:g} 日内；"
            "只改这个天数，金额、百分比、其他条款全部逐字保留。"
        )
    if dimension == "payment_ratio":
        return (
            f"把“支付至结算额的 X%”改为 {_format_percent(target_value)}；"
            "只改这个百分比，天数、其他条款全部逐字保留。"
        )
    if dimension == "prepay_ratio":
        return (
            f"把“预付款比例为 X%”改为 {_format_percent(target_value)}；"
            "只改这个百分比，其他条款全部逐字保留。"
        )
    if dimension == "performance_bond":
        return (
            f"把“履约保证金为合同金额的 X%”改为 {_format_percent(target_value)}；"
            "只改这个百分比，其他条款全部逐字保留。"
        )
    if dimension == "warranty_ratio":
        return (
            f"把“质保金为合同金额的 X%”改为 {_format_percent(target_value)}；"
            "只改这个百分比，其他条款全部逐字保留。"
        )
    if dimension == "tax_rate":
        return (
            f"把合同中的税率改为 {target_value:g}%（含税条款中的“含 X% 税”也同步改为 {target_value:g}%）；"
            "只改税率，其他条款全部逐字保留。"
        )
    raise ValueError(f"未知阈值维度: {dimension}")


SYNONYM_PROTECTED = (
    "背靠背", "合同专用章", "项目部章", "管辖", "保密", "知识产权", "数据",
    "验收", "工期", "承包范围", "计量", "质量标准", "自动续约",
    "单方解除", "扣罚", "违约", "保证金", "付款", "金额", "税率",
)


def _pick_synonym_clause(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
    """为同义改写挑一条“安全”的条款。

    同义改写必须有明确落点，否则模型会原样返回。这里不直接排除含数字/
    关键词的条款（否则很多短合同没有候选），而是给它们更高的风险排序；
    指令里要求保留数字和关键词，校验时再用 rule_hits / semantic 判断兜底。
    """
    if not parsed:
        return None
    # 50~600 字：太短改不动，太长容易混入表格/签署页/图片。
    candidates = [
        clause for clause in parsed.get("clauses", [])
        if 50 <= len(str(clause.get("text", "")).strip()) <= 600
    ]
    if not candidates:
        return None
    preferred_keywords = ("其他", "争议", "权利义务", "约定", "送达", "通知")

    def risk_score(clause: dict[str, Any]) -> tuple[int, int, int, int]:
        text = str(clause.get("text", ""))
        title = str(clause.get("title", ""))
        return (
            0 if any(item in title for item in preferred_keywords) else 1,
            0 if not re.search(r"\d", text) else 1,
            0 if not any(keyword in text for keyword in SYNONYM_PROTECTED) else 1,
            abs(len(text) - 150),
        )

    return min(candidates, key=risk_score)


def _text_instruction(
    dimension: str,
    target_hit: bool,
    contract_text: str,
    contract_index: int,
    parsed: dict[str, Any] | None = None,
) -> str:
    if dimension == "qualification":
        if target_hit:
            return (
                "如果合同已有资质条款，将其改写为："
                "“乙方为个人，不具备与本合同工程类型相符的施工资质和安全生产许可证。”"
                "如果合同没有资质条款，则在“乙方权利义务”或“其他约定”中新增这一条；"
                "其他条款、金额和格式全部保留。"
            )
        return (
            "如果合同已有“无资质/个人/不具备/未取得”等资质表述，将其改写为："
            "“乙方已取得与本合同工程类型相符的施工资质和安全生产许可证，资质证书编号为 DEMO-001。”"
            "如果合同没有资质条款，则在“乙方权利义务”或“其他约定”中新增这一条；"
            "其他条款、金额和格式全部保留。"
        )
    if dimension == "lease_method":
        if target_hit:
            return (
                "修改租赁/租金条款，删除计价单位、租金标准或计量方式，使租赁方式不明确；"
                "只改这一处，其他条款全部保留。"
            )
        return (
            "在租赁/租金条款中明确写入：“租金按实际使用天数、按吨计量，单价 5 元/吨/天，按月结算。”；"
            "如果已有租金条款，则把该条改为上述明确表述，其他条款全部保留。"
        )
    if dimension == "unfair_clause":
        if target_hit:
            return (
                "在“违约责任”或“其他约定”中新增一条："
                "“甲方有权随时单方解除本合同，乙方不得提出异议，由此造成的一切损失由乙方承担。”"
                "只新增这一条，其他条款全部保留。"
            )
        return (
            "如果合同已有“甲方有权随时解除/单方解除/乙方不得提出异议/一切损失由乙方承担/概不负责”等表述，"
            "将其改写为：“双方协商一致可以解除本合同，提前解除的补偿按实际损失由双方对等承担。”"
            "如果合同没有这类表述，则在“违约责任”或“其他约定”中新增这一条；其他条款全部保留。"
        )
    if dimension == "risk_clause":
        if target_hit:
            return (
                "在“风险”或“责任”条款中新增一条："
                "“本合同履行过程中的所有风险及全部损失由乙方承担，甲方不承担任何责任。”"
                "只新增这一条，其他条款全部保留。"
            )
        return (
            "如果合同已有“所有风险/全部损失由乙方承担/甲方不承担任何责任”等表述，"
            "将其改写为：“本合同履行过程中的风险由双方按责任比例共担，损失按实际责任划分。”"
            "如果合同没有这类表述，则在“风险”或“责任”条款中新增这一条；其他条款全部保留。"
        )
    if dimension == "early_exit":
        if target_hit:
            return (
                "在“退场”“解除”或“违约责任”条款中新增一条："
                "“乙方提前退场的，已支付款项不予退还，并应赔偿甲方全部损失；甲方提前终止的，不承担任何责任。”"
                "只新增这一条，其他条款全部保留。"
            )
        return (
            "如果合同已有“乙方提前退场不予退还/甲方提前终止不承担责任/全部损失由乙方承担”等表述，"
            "将其改写为：“任何一方提前终止合同的，应提前 15 日书面通知，并按实际损失对等赔偿。”"
            "如果合同没有这类表述，则在“退场”“解除”或“违约责任”条款中新增这一条；其他条款全部保留。"
        )
    if dimension == "bid_bond":
        if target_hit:
            return (
                "在付款或保证金条款中新增/修改一条：“投标保证金为合同金额的 15%。”；"
                "只改这一处，其他条款全部保留。"
            )
        return (
            "在付款或保证金条款中新增/修改一条："
            "“投标保证金为合同金额的 2%，未中标后 10 日内退还。”；"
            "只改这一处，其他条款全部保留。"
        )
    if dimension == "party_names":
        if "租赁" in contract_text:
            old_a, old_b, new_a, new_b = "甲方", "乙方", "出租方", "承租方"
        elif "买卖" in contract_text:
            old_a, old_b, new_a, new_b = "甲方", "乙方", "买方", "卖方"
        else:
            old_a, old_b, new_a, new_b = "甲方", "乙方", "发包方", "承包方"
        return (
            f"全文将“{old_a}”替换为“{new_a}”，“{old_b}”替换为“{new_b}”；"
            "保持句子结构、金额、百分比和条款数量不变。"
        )
    if dimension == "synonym_rewrite":
        clause = _pick_synonym_clause(parsed)
        if clause is None:
            raise ValueError("没有适合同义改写的条款")
        clause_no = clause.get("clause_no", "该条")
        clause_text = re.sub(r"\s+", " ", str(clause.get("text", ""))).strip()
        if len(clause_text) > 240:
            clause_text = clause_text[:240].rstrip() + "…"
        return (
            f"只改写第 {clause_no} 条（原文：{clause_text}）为同义表达，"
            "至少改动该条 30% 的字词；其他条款逐字保留；"
            "不得改变数字、金额、比例、日期，也不得增删或替换以下关键词："
            "背靠背、合同专用章、项目部章、管辖、保密、知识产权、数据、"
            "验收、工期、承包范围、计量、质量标准、自动续约、单方解除、扣罚、违约。"
            "如果无法改写，请明确说明哪一句无法同义表达。"
        )
    raise ValueError(f"未知文本维度: {dimension}")


def _is_applicable(
    dimension: str,
    contract_text: str,
    normalized: dict[str, float],
    parsed: dict[str, Any] | None = None,
) -> bool:
    if dimension == "payment_days":
        return any(word in contract_text for word in ("付款", "支付", "结算"))
    if dimension == "payment_ratio":
        return any(word in contract_text for word in ("付款比例", "结算额", "支付至", "付款至"))
    if dimension == "prepay_ratio":
        return "预付" in contract_text
    if dimension == "performance_bond":
        return "履约保证金" in contract_text and "投标保证金" not in contract_text
    if dimension == "bid_bond":
        # 没有投标保证金条款也可以新增；租赁合同不强行加投标保证金。
        return "租赁" not in contract_text
    if dimension == "warranty_ratio":
        return "质保金" in contract_text
    if dimension == "tax_rate":
        return any(word in contract_text for word in ("税率", "税金", "含税", "含 "))
    if dimension == "contract_amount":
        return normalized.get("contract_amount") is not None
    if dimension == "qualification":
        return any(word in contract_text for word in ("施工", "分包", "工程", "承包"))
    if dimension == "lease_method":
        return "租赁" in contract_text
    if dimension == "party_names":
        return "甲方" in contract_text and "乙方" in contract_text
    if dimension == "synonym_rewrite":
        return _pick_synonym_clause(parsed) is not None
    return True


def _similarity(left: str, right: str) -> float:
    normalize = lambda text: "".join(str(text).split())
    return difflib.SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def _value_close(actual: float | None, expected: float) -> bool:
    if actual is None:
        return False
    if expected == 0:
        return abs(actual) < 1e-9
    return abs(actual - expected) / abs(expected) <= 0.05


def _extract_bid_bond_ratio(text: str) -> float | None:
    match = re.search(r"投标保证金[^。；\n]{0,24}?(\d+(?:\.\d+)?)\s*%", text)
    return float(match.group(1)) / 100 if match else None


def _find_clause_by_no(parsed: dict[str, Any], clause_no: Any) -> dict[str, Any] | None:
    target = str(clause_no or "").strip()
    for clause in parsed.get("clauses", []):
        if str(clause.get("clause_no", "")).strip() == target:
            return clause
    return None


def validate_variant(
    *,
    dimension: str,
    original_text: str,
    variant_text: str,
    original_parsed: dict[str, Any],
    original_hits: set[str],
    original_labels: dict[str, dict[str, Any]],
    target_value: float | None,
    target_hit: bool | None,
    rules: list[dict[str, Any]],
    file_name: str,
) -> tuple[list[str], dict[str, Any], set[str], dict[str, dict[str, Any]], float, float]:
    errors: list[str] = []
    try:
        parsed = parse_contract(variant_text, file_name)
    except Exception as exc:
        return [f"parse_contract 失败: {exc}"], {}, set(), {}, 0.0, 0.0

    if len(parsed.get("clauses", [])) < C.VARIANT_MIN_CLAUSES:
        errors.append(f"条款数少于 {C.VARIANT_MIN_CLAUSES}")

    original_hashes = _clause_hashes(original_parsed)
    variant_hashes = _clause_hashes(parsed)
    unchanged_ratio = (
        len(original_hashes & variant_hashes) / len(original_hashes)
        if original_hashes
        else 1.0
    )
    if dimension == "party_names":
        min_ratio = C.VARIANT_PARTY_MIN_UNCHANGED_CLAUSE_RATIO
    elif dimension == "synonym_rewrite":
        # 同义改写可能改到多条款，由全文相似度兜底。
        min_ratio = 0.0
    elif dimension in TEXT_DIMENSIONS or dimension == "bid_bond":
        # 文本维度会新增/改写一条条款，短合同允许 1 条变化。
        min_ratio = min(C.VARIANT_MIN_UNCHANGED_CLAUSE_RATIO, 0.4)
    else:
        min_ratio = C.VARIANT_MIN_UNCHANGED_CLAUSE_RATIO
    if unchanged_ratio < min_ratio:
        errors.append(f"未改动条款保留率过低: {unchanged_ratio:.2f} < {min_ratio}")

    similarity = _similarity(original_text, variant_text)
    if dimension == "synonym_rewrite":
        # 长合同只改一条短条款时，全文相似度仍然接近 1.0；
        # 因此这里改看“目标条款”的相似度，全文相似度只做下限保护。
        if similarity < C.VARIANT_SYNONYM_MIN_SIMILARITY:
            errors.append(f"同义改写全文相似度过低: {similarity:.2f}")
        target_clause = _pick_synonym_clause(original_parsed)
        if target_clause is None:
            errors.append("原文没有适合同义改写的条款")
        else:
            variant_clause = _find_clause_by_no(parsed, target_clause.get("clause_no"))
            if variant_clause is None:
                errors.append("变体中没有找到目标条款，无法核对同义改写")
            else:
                clause_similarity = _similarity(
                    str(target_clause.get("text", "")),
                    str(variant_clause.get("text", "")),
                )
                if not (C.VARIANT_SYNONYM_MIN_SIMILARITY <= clause_similarity <= 0.95):
                    errors.append(
                        f"同义改写目标条款相似度不在允许区间: {clause_similarity:.2f}"
                    )
    elif similarity < C.VARIANT_MIN_SIMILARITY:
        errors.append(f"全文相似度过低: {similarity:.2f} < {C.VARIANT_MIN_SIMILARITY}")

    normalized = extract_normalized(variant_text)
    hits = rule_hit_codes(parsed, rules, file_name)
    labels = semantic_profile(parsed, rules)

    if dimension in THRESHOLD_DIMENSIONS:
        rule_code = THRESHOLD_DIMENSIONS[dimension]
        rule = next(item for item in rules if item["rule_code"] == rule_code)
        field = rule.get("target_field")
        if field is None or target_value is None:
            errors.append("阈值维度缺 target_field/target_value")
        else:
            if not _value_close(normalized.get(field), target_value):
                errors.append(f"{field} 未改到目标值 {target_value}: {normalized.get(field)}")
            symmetric_difference = original_hits ^ hits
            if symmetric_difference != {rule_code}:
                errors.append(
                    f"rule_hits 应恰好翻转 {rule_code}，实际差异 {sorted(symmetric_difference)}"
                )
    elif dimension == "contract_amount":
        if target_value is None or not _value_close(normalized.get("contract_amount"), target_value):
            errors.append(f"contract_amount 未改到目标值 {target_value}: {normalized.get('contract_amount')}")
        if hits != original_hits:
            errors.append(f"合同金额变体不应改变 rule_hits: {sorted(hits ^ original_hits)}")
    elif dimension == "party_names":
        original_a = original_text.count("甲方")
        original_b = original_text.count("乙方")
        if original_a <= variant_text.count("甲方") and original_b <= variant_text.count("乙方"):
            errors.append("当事人称呼没有发生替换")
        if hits != original_hits:
            errors.append(f"当事人称呼变体不应改变 rule_hits: {sorted(hits ^ original_hits)}")
    elif dimension == "synonym_rewrite":
        if hits != original_hits:
            errors.append(f"同义改写不应改变 rule_hits: {sorted(hits ^ original_hits)}")
        original_label_hits = {code: bool(label["hit"]) for code, label in original_labels.items()}
        variant_label_hits = {code: bool(label["hit"]) for code, label in labels.items()}
        if original_label_hits != variant_label_hits:
            errors.append("同义改写不应改变语义规则判断")
    elif dimension == "bid_bond":
        if target_value is None:
            errors.append("bid_bond 缺 target_value")
        else:
            bid_ratio = _extract_bid_bond_ratio(variant_text)
            if not _value_close(bid_ratio, target_value):
                errors.append(f"投标保证金比例未改到 {target_value}: {bid_ratio}")
            if target_value > 0.1:
                semantic_hit = bool(labels.get("R-SEM-09", {}).get("hit"))
                if "R-DEP-01" not in hits and not semantic_hit:
                    errors.append("投标保证金超过 10% 但 R-DEP-01 / R-SEM-09 都未命中")
            else:
                label = labels.get("R-SEM-09")
                if label is None or label["hit"] is not False:
                    errors.append("低比例投标保证金未形成 R-SEM-09 的“条款完整”判断")
    else:
        rule_code = TEXT_DIMENSIONS.get(dimension)
        if rule_code is None:
            errors.append(f"未知维度: {dimension}")
        else:
            label = labels.get(rule_code)
            if label is None:
                errors.append(f"{rule_code} 未能标注（条款可能没有被解析出来）")
            elif target_hit is not None and bool(label["hit"]) != bool(target_hit):
                errors.append(f"{rule_code} 期望 hit={target_hit}，实际 {label['hit']}")

    return errors, parsed, hits, labels, unchanged_ratio, similarity


def build_plan(
    base_paths: list[Path],
    parsed_map: dict[Path, dict[str, Any]],
    rules: list[dict[str, Any]],
    variants_per_contract: int,
    enabled_dimensions: list[str] | None,
    max_contracts: int | None,
    train_only: bool,
) -> list[dict[str, Any]]:
    split_map = assign_contract_splits(base_paths, parsed_map)
    eligible = [
        path for path in base_paths
        if (not train_only or split_map[path] == "train")
    ]
    if max_contracts:
        eligible = eligible[:max_contracts]

    dimension_order = enabled_dimensions or list(C.VARIANT_DIMENSION_ORDER)
    dimension_cursor = 0
    plan: list[dict[str, Any]] = []
    for contract_index, path in enumerate(eligible):
        text = read_contract_text(path)
        if len(text.encode("utf-8")) > C.VARIANT_MAX_SOURCE_BYTES:
            print(f"[跳过] {path.name} 超过 {C.VARIANT_MAX_SOURCE_BYTES} 字节")
            continue
        parsed = parsed_map[path]
        normalized = extract_normalized(text)
        original_hits = rule_hit_codes(parsed, rules, path.name)
        original_labels = semantic_profile(parsed, rules)

        # 全局轮转维度，保证 9 份合同合起来能覆盖到所有维度（包括资质/租赁/
        # 霸王条款/风险条款/提前退场/投标保证金）。
        chosen: list[str] = []
        scanned = 0
        while len(chosen) < variants_per_contract and scanned < len(dimension_order) * 2:
            dimension = dimension_order[dimension_cursor % len(dimension_order)]
            dimension_cursor += 1
            scanned += 1
            if dimension in chosen:
                continue
            if not _is_applicable(dimension, text, normalized, parsed):
                continue
            chosen.append(dimension)
        if not chosen:
            continue
        for offset, dimension in enumerate(chosen):
            target_value = None
            target_hit = None
            if dimension in THRESHOLD_DIMENSIONS:
                rule_code = THRESHOLD_DIMENSIONS[dimension]
                rule = next(item for item in rules if item["rule_code"] == rule_code)
                current_hit = rule_code in original_hits
                target_value = _target_for_threshold(dimension, current_hit)
                target_hit = not current_hit
                instruction = _threshold_instruction(dimension, rule, target_value, target_hit)
            elif dimension == "bid_bond":
                current_hit = "R-DEP-01" in original_hits or bool(
                    original_labels.get("R-SEM-09", {}).get("hit")
                )
                target_value = BID_BOND_VALUES["risk" if not current_hit else "safe"]
                target_hit = not current_hit
                instruction = _text_instruction(dimension, target_hit, text, contract_index, parsed)
            elif dimension == "contract_amount":
                amount = normalized.get("contract_amount")
                if amount is None:
                    continue
                target_value = float(round(amount * 1.5, -4))
                target_hit = None
                instruction = (
                    f"把合同金额/暂定总价改为 {int(target_value)} 元；"
                    "大写金额可不改或按新金额改写，其他条款全部逐字保留。"
                )
            elif dimension == "party_names":
                instruction = _text_instruction(dimension, False, text, contract_index, parsed)
            elif dimension == "synonym_rewrite":
                instruction = _text_instruction(dimension, False, text, contract_index, parsed)
            else:
                rule_code = TEXT_DIMENSIONS[dimension]
                current = original_labels.get(rule_code, {}).get("hit")
                target_hit = (not current) if current is not None else ((contract_index + offset) % 2 == 0)
                instruction = _text_instruction(dimension, bool(target_hit), text, contract_index, parsed)
            plan.append(
                {
                    "path": path,
                    "contract_index": contract_index,
                    "variant_index": offset + 1,
                    "dimension": dimension,
                    "target_value": target_value,
                    "target_hit": target_hit,
                    "instruction": instruction,
                    "text": text,
                    "parsed": parsed,
                    "hits": original_hits,
                    "labels": original_labels,
                }
            )
    return plan


def variant_filename(parent: Path, variant_index: int) -> str:
    return f"{parent.stem}__v{variant_index:02d}{parent.suffix}"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    C.ensure_output_dirs()
    base_paths = load_base_paths(args.raw_dir)
    parsed_map = parse_all(base_paths)
    rules = load_rules()

    enabled = None
    if args.dimensions:
        enabled = [item.strip() for item in args.dimensions.split(",") if item.strip()]
        unknown = sorted(set(enabled) - set(C.VARIANT_DIMENSION_ORDER))
        if unknown:
            raise ValueError(f"未知维度: {unknown}")

    plan = build_plan(
        base_paths,
        parsed_map,
        rules,
        args.variants_per_contract,
        enabled,
        args.max_contracts,
        C.VARIANT_TRAIN_ONLY,
    )
    if not plan:
        raise RuntimeError("没有可生成的变体计划；检查合同目录和维度开关")

    existing_manifest = (
        read_jsonl(C.VARIANT_MANIFEST_PATH)
        if args.resume and C.VARIANT_MANIFEST_PATH.exists()
        else []
    )
    manifest_by_file = {
        row["variant_file"]: row
        for row in existing_manifest
        if row.get("variant_file")
    }
    client = None if args.dry_run else get_client()
    generated = 0
    skipped = 0

    for item in plan:
        parent: Path = item["path"]
        output = args.variant_dir / variant_filename(parent, item["variant_index"])
        existing_row = manifest_by_file.get(output.name)
        # --resume 不仅跳过已存在文件，也跳过上次明确失败的条目；
        # 想重试失败项时加 --force。
        if (
            args.resume
            and not args.force
            and not args.retry_skipped
            and existing_row
            and existing_row.get("status") == "skipped"
        ):
            print(
                f"[skip] {output.name} 上次已标记 skipped；"
                "如已修 prompt，请用 --resume --retry-skipped 重试"
            )
            skipped += 1
            continue
        if output.exists() and not args.force:
            if args.resume:
                skipped += 1
                continue
            raise FileExistsError(f"变体已存在；加 --resume 或 --force: {output}")

        prompt = variant_prompt(
            contract_name=parent.name,
            dimension=item["dimension"],
            instruction=item["instruction"],
            contract_text=item["text"],
        )
        if args.dry_run:
            print(
                f"[dry-run] {parent.name} -> {output.name} "
                f"dimension={item['dimension']} prompt_chars={len(prompt)} "
                f"target={item['target_value']} target_hit={item['target_hit']}"
            )
            continue

        last_errors: list[str] = []
        accepted = False
        temperature = (
            C.VARIANT_SYNONYM_TEMPERATURE
            if item["dimension"] == "synonym_rewrite"
            else C.VARIANT_TEMPERATURE
        )
        for attempt in range(1, C.VARIANT_RETRIES + 1):
            attempt_prompt = prompt
            if attempt > 1 and last_errors:
                # 把上一轮被拒原因反馈给模型，避免它重复原样输出。
                feedback = "\n".join(f"- {error}" for error in last_errors[:4])
                attempt_prompt = (
                    prompt
                    + "\n\n## 上一次输出被拒原因\n"
                    + feedback
                    + "\n请针对上述原因重写，必须真正改动指令指定的条款。"
                )
            try:
                payload = client.chat_json(
                    attempt_prompt,
                    temperature=temperature,
                    max_tokens=C.VARIANT_MAX_TOKENS,
                )
            except DeepSeekError as exc:
                last_errors = [str(exc)]
                continue
            variant_text = payload.get("contract_text")
            if not isinstance(variant_text, str) or not variant_text.strip():
                last_errors = ["模型没有返回 contract_text"]
                continue
            errors, _, variant_hits, variant_labels, unchanged_ratio, similarity = validate_variant(
                dimension=item["dimension"],
                original_text=item["text"],
                variant_text=variant_text,
                original_parsed=item["parsed"],
                original_hits=item["hits"],
                original_labels=item["labels"],
                target_value=item["target_value"],
                target_hit=item["target_hit"],
                rules=rules,
                file_name=output.name,
            )
            if errors:
                last_errors = errors
                print(f"  [第 {attempt} 次被拒] {output.name}: {errors[:2]}")
                continue
            args.variant_dir.mkdir(parents=True, exist_ok=True)
            output.write_text(variant_text, encoding="utf-8")
            manifest_by_file[output.name] = {
                "parent": parent.name,
                "variant_file": output.name,
                "dimension": item["dimension"],
                "target_value": item["target_value"],
                "target_hit": item["target_hit"],
                "status": "ok",
                "reason": "",
                "orig_rule_hits": sorted(item["hits"]),
                "var_rule_hits": sorted(variant_hits),
                "var_semantic_hits": {
                    code: bool(label["hit"]) for code, label in variant_labels.items()
                },
                "unchanged_clause_ratio": round(unchanged_ratio, 4),
                "similarity": round(similarity, 4),
            }
            generated += 1
            accepted = True
            print(f"[ok] {output.name} dimension={item['dimension']}")
            break
        if not accepted:
            manifest_by_file[output.name] = {
                "parent": parent.name,
                "variant_file": output.name,
                "dimension": item["dimension"],
                "target_value": item["target_value"],
                "target_hit": item["target_hit"],
                "status": "skipped",
                "reason": "; ".join(last_errors[:3]),
                "orig_rule_hits": sorted(item["hits"]),
                "unchanged_clause_ratio": None,
                "similarity": None,
            }
            print(f"[skip] {output.name}: {last_errors[:2]}")

    if not args.dry_run:
        write_jsonl(C.VARIANT_MANIFEST_PATH, list(manifest_by_file.values()))
        print(
            json.dumps(
                {
                    "planned": len(plan),
                    "generated": generated,
                    "skipped_existing": skipped,
                    "variant_dir": str(args.variant_dir),
                    "manifest": str(C.VARIANT_MANIFEST_PATH),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"\n[dry-run] 计划 {len(plan)} 个变体，未调用 DeepSeek。")


if __name__ == "__main__":
    main()
