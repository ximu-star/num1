"""评估公共工具：模型加载、生成、JSON 解析与规则指标。"""

from __future__ import annotations

import json
import re
from typing import Any

import config as C
from core.io_utils import compact_json
from core.report import (
    CLAUSE_NO_RE,
    NUMBER_RE,
    fact_violations,
    validate_reject_output,
    validate_report,
    validate_semantic_output,
)

RULE_CODE_RE = re.compile(r"R-[A-Z]+-\d+")
CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def load_model(model_path):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not model_path.is_dir():
        raise FileNotFoundError(f"找不到模型目录: {model_path}")
    dtype_name = C.EVAL_DTYPE
    if (
        torch.cuda.is_available()
        and dtype_name == "bfloat16"
        and not torch.cuda.is_bf16_supported()
    ):
        dtype_name = "float16"
    dtype = {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }[dtype_name]
    if C.EVAL_DEVICE == "auto":
        device_map = "auto" if torch.cuda.is_available() else None
    else:
        device_map = None
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    if not tokenizer.chat_template and C.CHAT_TEMPLATE_PATH.exists():
        tokenizer.chat_template = C.CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        local_files_only=True,
        dtype=dtype,
        device_map=device_map,
        low_cpu_mem_usage=True,
    )
    if C.EVAL_DEVICE not in {"auto"}:
        model = model.to(C.EVAL_DEVICE)
    model.eval()
    return model, tokenizer, torch


def build_prompt(tokenizer, snapshot: dict[str, Any]) -> str:
    return tokenizer.apply_chat_template(
        [{"role": "user", "content": compact_json(snapshot)}],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )


def generate_text(model, tokenizer, torch_module, snapshot: dict[str, Any], max_new_tokens: int) -> str:
    prompt = build_prompt(tokenizer, snapshot)
    inputs = tokenizer(prompt, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch_module.inference_mode():
        output_ids = model.generate(
            **inputs,
            do_sample=False,
            max_new_tokens=max_new_tokens,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    generated = output_ids[0][inputs["input_ids"].shape[1] :]
    text = tokenizer.decode(generated, skip_special_tokens=True).strip()
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()
    return text


def extract_json(text: str) -> dict[str, Any] | None:
    cleaned = CODE_FENCE_RE.sub("", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_ws(text: str) -> str:
    return "".join(str(text).split())


def _clause_texts(snapshot: dict[str, Any]) -> list[str]:
    return [_normalize_ws(clause.get("text", "")) for clause in snapshot.get("clauses", [])]


def _valid_positions(snapshot: dict[str, Any]) -> set[str]:
    positions = {"全文", "合同正文"}
    for clause in snapshot.get("clauses", []):
        positions.add(f"{clause.get('clause_no')}（第{clause.get('page', 1)}页）")
    positions.update(str(hit.get("evidence_position")) for hit in snapshot.get("rule_hits", []))
    return positions


def _focus_ref_provenanced(ref: Any, snapshot: dict[str, Any]) -> bool:
    """判断一条 focus_point_ref 是否能回溯到输入规则/语义候选。"""
    if not isinstance(ref, dict):
        return False
    kind = ref.get("kind")
    code = ref.get("rule_code")
    position = ref.get("evidence_position")
    if kind == "rule":
        source = next(
            (hit for hit in snapshot.get("rule_hits", []) if hit.get("rule_code") == code),
            None,
        )
        return source is not None and position == source.get("evidence_position")
    if kind == "semantic":
        exists = any(
            item.get("rule_code") == code
            for item in snapshot.get("semantic_candidates", [])
        )
        return exists and position in _valid_positions(snapshot)
    return False


def _f1(pred: set[str], gold: set[str]) -> tuple[float, float, float]:
    if not pred and not gold:
        return 1.0, 1.0, 1.0
    true_positive = len(pred & gold)
    precision = true_positive / len(pred) if pred else 0.0
    recall = true_positive / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def score_sample(pred: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any]:
    snapshot = row["input_snapshot"]
    expected = row["expected_report"]
    sample_type = row["sample_type"]
    metrics: dict[str, Any] = {
        "json_valid": pred is not None,
        "schema_valid": False,
        "rule_precision": None,
        "rule_recall": None,
        "rule_f1": None,
        "evidence_rate": None,
        "position_accuracy": None,
        "risk_accuracy": None,
        "semantic_accuracy": None,
        "focus_provenance_rate": None,
        "fabrication_count": None,
        "fabrication_rate": None,
        "reject_accuracy": None,
        "reject_type_accuracy": None,
        "summary_len": None,
    }
    if pred is None:
        return metrics

    if sample_type == "review":
        errors = validate_report(pred, snapshot, row)
        metrics["schema_valid"] = not errors
        gold_items = expected.get("hit_rules") if isinstance(expected.get("hit_rules"), list) else []
        gold_codes = {
            hit["rule_code"] for hit in gold_items
            if isinstance(hit, dict) and "rule_code" in hit
        }
        pred_items = pred.get("hit_rules") if isinstance(pred.get("hit_rules"), list) else []
        pred_codes = {
            hit.get("rule_code") for hit in pred_items
            if isinstance(hit, dict) and isinstance(hit.get("rule_code"), str)
        }
        precision, recall, f1 = _f1(pred_codes, gold_codes)
        metrics.update(rule_precision=precision, rule_recall=recall, rule_f1=f1)
        metrics["risk_accuracy"] = pred.get("overall_risk_level") == expected.get("overall_risk_level")

        input_hits = {hit["rule_code"]: hit for hit in snapshot.get("rule_hits", [])}
        clause_texts = _clause_texts(snapshot)
        gold_hits = [hit for hit in gold_items if isinstance(hit, dict)]
        gold_positions = {hit.get("rule_code"): hit.get("evidence_position") for hit in gold_hits}
        evidence_ok = 0
        position_ok = 0
        pred_hits = [hit for hit in pred.get("hit_rules", []) if isinstance(hit, dict)]
        for hit in pred_hits:
            code = hit.get("rule_code")
            evidence = _normalize_ws(hit.get("evidence_text", ""))
            source = input_hits.get(code)
            traceable = any(evidence in text for text in clause_texts) if evidence else False
            if source is not None and evidence == _normalize_ws(source.get("evidence_text", "")):
                traceable = True
            evidence_ok += int(traceable)
            position_ok += int(hit.get("evidence_position") == gold_positions.get(code))
        metrics["evidence_rate"] = (
            evidence_ok / len(pred_hits) if pred_hits else (1.0 if not gold_hits else 0.0)
        )
        metrics["position_accuracy"] = (
            position_ok / len(pred_hits) if pred_hits else (1.0 if not gold_hits else 0.0)
        )

        expected_sem_items = (
            row.get("expected_semantic") if isinstance(row.get("expected_semantic"), list) else []
        )
        expected_sem = {
            item["rule_code"]: bool(item["hit"])
            for item in expected_sem_items
            if isinstance(item, dict) and "rule_code" in item
        }
        pred_sem_items = (
            pred.get("semantic_judgements")
            if isinstance(pred.get("semantic_judgements"), list)
            else []
        )
        pred_sem = {
            item.get("rule_code"): bool(item.get("hit"))
            for item in pred_sem_items
            if isinstance(item, dict)
        }
        if expected_sem:
            metrics["semantic_accuracy"] = (
                sum(1 for code, value in expected_sem.items() if pred_sem.get(code) == value) / len(expected_sem)
            )
        else:
            metrics["semantic_accuracy"] = 1.0 if not pred_sem else 0.0
        pred_focus = pred.get("focus_points") if isinstance(pred.get("focus_points"), list) else []
        pred_refs = (
            pred.get("focus_point_refs")
            if isinstance(pred.get("focus_point_refs"), list)
            else []
        )
        provenanced = sum(
            1 for ref in pred_refs if _focus_ref_provenanced(ref, snapshot)
        )
        metrics["focus_provenance_rate"] = (
            provenanced / len(pred_focus) if pred_focus else 0.0
        )
        text = json.dumps(pred, ensure_ascii=False)
        codes, numbers, clauses = fact_violations(text, snapshot, row, pred)
        metrics["fabrication_count"] = len(codes) + len(numbers) + len(clauses)
        observed = (
            len(RULE_CODE_RE.findall(text))
            + len(NUMBER_RE.findall(text))
            + len(CLAUSE_NO_RE.findall(text))
        )
        metrics["fabrication_rate"] = metrics["fabrication_count"] / observed if observed else 0.0
        metrics["summary_len"] = len(str(pred.get("summary", "")))
        metrics["schema_errors"] = errors
    elif sample_type == "semantic":
        errors = validate_semantic_output(pred, snapshot, row)
        metrics["schema_valid"] = not errors
        expected_sem_items = (
            row.get("expected_semantic") if isinstance(row.get("expected_semantic"), list) else []
        )
        expected_sem = {
            item["rule_code"]: bool(item["hit"])
            for item in expected_sem_items
            if isinstance(item, dict) and "rule_code" in item
        }
        pred_sem_items = (
            pred.get("semantic_judgements")
            if isinstance(pred.get("semantic_judgements"), list)
            else []
        )
        pred_sem = {
            item.get("rule_code"): bool(item.get("hit"))
            for item in pred_sem_items
            if isinstance(item, dict)
        }
        metrics["semantic_accuracy"] = (
            sum(1 for code, value in expected_sem.items() if pred_sem.get(code) == value) / len(expected_sem)
            if expected_sem else 0.0
        )
        text = json.dumps(pred, ensure_ascii=False)
        codes, numbers, clauses = fact_violations(text, snapshot, row, {"hit_rules": []})
        metrics["fabrication_count"] = len(codes) + len(numbers) + len(clauses)
        observed = (
            len(RULE_CODE_RE.findall(text))
            + len(NUMBER_RE.findall(text))
            + len(CLAUSE_NO_RE.findall(text))
        )
        metrics["fabrication_rate"] = metrics["fabrication_count"] / observed if observed else 0.0
        metrics["schema_errors"] = errors
    else:
        errors = validate_reject_output(pred)
        metrics["schema_valid"] = not errors
        metrics["reject_accuracy"] = not errors
        metrics["reject_type_accuracy"] = (
            pred.get("reject_type") == expected.get("reject_type")
            if isinstance(pred, dict) else False
        )
        metrics["schema_errors"] = errors
    return metrics


def aggregate(details: list[dict[str, Any]]) -> dict[str, Any]:
    numeric_keys = (
        "rule_precision", "rule_recall", "rule_f1", "evidence_rate",
        "position_accuracy", "risk_accuracy", "semantic_accuracy",
        "focus_provenance_rate",
        "fabrication_count", "fabrication_rate", "summary_len",
    )
    bool_keys = ("json_valid", "schema_valid", "reject_accuracy", "reject_type_accuracy")
    result: dict[str, Any] = {"count": len(details), "denominators": {}}
    for key in bool_keys:
        values = [item[key] for item in details if item.get(key) is not None]
        result[key] = sum(1 for value in values if value) / len(values) if values else None
        result["denominators"][key] = len(values)
    for key in numeric_keys:
        values = [item[key] for item in details if item.get(key) is not None]
        result[key] = sum(values) / len(values) if values else None
        result["denominators"][key] = len(values)
    return result
