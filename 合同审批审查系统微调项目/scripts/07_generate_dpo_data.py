"""用 SFT 模型采样候选报告，构造 DPO chosen/rejected。

与老师 07 的差异：
- 只从 train SFT 数据取题，绝不使用 validation / final_test；
- prompt 是完整 input_snapshot，不是一句用户问题；
- chosen/rejected 由规则指标决定，Judge 只在硬指标同分时可选细排；
- 至少一项硬指标不同才保留，禁止“更长就更好”。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from core.deepseek import DeepSeekError, get_client  # noqa: E402
from core.eval_utils import extract_json, score_sample  # noqa: E402
from core.io_utils import (  # noqa: E402
    append_jsonl,
    load_existing_ids,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from core.prompts import dpo_judge_prompt  # noqa: E402

# 不允许 schema_valid 单独作为差异，避免只因为长度/格式不同就保留偏好对。
HARD_FIELDS_REVIEW = (
    "rule_f1", "evidence_rate", "position_accuracy", "risk_accuracy",
    "semantic_accuracy", "focus_provenance_rate", "fabrication_count",
)
HARD_FIELDS_SEMANTIC = ("semantic_accuracy", "fabrication_count")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构造合同审查 DPO 偏好数据")
    parser.add_argument("--model", type=Path, default=C.DPO_GENERATION_MODEL)
    parser.add_argument("--sft-data", type=Path, default=C.SFT_DATA_DIR / "sft_train.jsonl")
    parser.add_argument("--bad-cases", type=Path, default=C.DPO_BAD_CASE_FILE)
    parser.add_argument("--output-dir", type=Path, default=C.DPO_DATA_DIR)
    parser.add_argument("--max-prompts", type=int, default=C.DPO_MAX_PROMPTS)
    parser.add_argument("--num-candidates", type=int, default=C.DPO_NUM_CANDIDATES)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def load_prompts(path: Path, bad_cases: Path | None) -> list[dict[str, Any]]:
    manifest_path = C.MANIFEST_DIR / "train_manifest.json"
    train_ids: set[str] = set()
    if manifest_path.exists():
        train_ids = set(read_json(manifest_path).get("seed_ids") or [])

    rows = read_jsonl(path)
    if bad_cases is not None:
        rows.extend(read_jsonl(bad_cases))
    selected = []
    seen: set[str] = set()
    for row in rows:
        if row.get("sample_type") not in {"review", "semantic"}:
            continue
        # DPO 题源硬校验：只允许 train，且 seed_id 必须属于 train manifest。
        if row.get("split") != "train":
            raise ValueError(f"{row.get('sample_id')} 不是 train 样本，禁止进入 DPO")
        if train_ids and row.get("seed_id") not in train_ids:
            raise ValueError(f"{row.get('sample_id')} 的 seed_id 不在 train manifest 中")
        messages = row.get("messages")
        if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
            continue
        prompt = messages[0].get("content", "")
        if not isinstance(prompt, str) or not prompt or prompt in seen:
            continue
        seen.add(prompt)
        selected.append(row)
    return selected


def load_generation_model(path: Path):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    if not tokenizer.chat_template and C.CHAT_TEMPLATE_PATH.exists():
        tokenizer.chat_template = C.CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    if torch.cuda.is_available():
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    else:
        dtype = torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        path,
        local_files_only=True,
        dtype=dtype,
        device_map="auto" if torch.cuda.is_available() else None,
        low_cpu_mem_usage=True,
    )
    model.eval()
    return model, tokenizer, torch


def sample_candidates(
    model,
    tokenizer,
    torch_module,
    prompt: str,
    *,
    count: int,
    seed: int,
) -> list[str]:
    prompt_text = tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt}],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    inputs = tokenizer(prompt_text, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {key: value.to(device) for key, value in inputs.items()}
    candidates: list[str] = []
    for offset in range(C.DPO_MAX_SAMPLE_ATTEMPTS):
        torch_module.manual_seed(seed + offset)
        with torch_module.inference_mode():
            output_ids = model.generate(
                **inputs,
                do_sample=True,
                temperature=C.DPO_CANDIDATE_TEMPERATURE,
                top_p=C.DPO_CANDIDATE_TOP_P,
                max_new_tokens=C.DPO_MAX_NEW_TOKENS,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        text = tokenizer.decode(
            output_ids[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
        ).strip()
        if text and text not in candidates:
            candidates.append(text)
        if len(candidates) >= count:
            break
    return candidates


def _fabrication_penalty(metrics: dict[str, Any]) -> int:
    count = metrics.get("fabrication_count")
    # 0 是合法且最好的取值，不能用 `or 99` 把 0 当成缺失。
    return -(count if count is not None else 99)


def composite_key(metrics: dict[str, Any], sample_type: str) -> tuple:
    if sample_type == "review":
        return (
            int(bool(metrics.get("schema_valid"))),
            metrics.get("rule_f1") if metrics.get("rule_f1") is not None else 0.0,
            metrics.get("evidence_rate") if metrics.get("evidence_rate") is not None else 0.0,
            int(bool(metrics.get("risk_accuracy"))),
            metrics.get("semantic_accuracy") if metrics.get("semantic_accuracy") is not None else 0.0,
            metrics.get("focus_provenance_rate") if metrics.get("focus_provenance_rate") is not None else 0.0,
            _fabrication_penalty(metrics),
        )
    return (
        int(bool(metrics.get("schema_valid"))),
        metrics.get("semantic_accuracy") if metrics.get("semantic_accuracy") is not None else 0.0,
        _fabrication_penalty(metrics),
    )


def hard_diff(metrics_a: dict[str, Any], metrics_b: dict[str, Any], sample_type: str) -> bool:
    fields = HARD_FIELDS_REVIEW if sample_type == "review" else HARD_FIELDS_SEMANTIC
    return any(metrics_a.get(field) != metrics_b.get(field) for field in fields)


def build_row(
    row: dict[str, Any],
    candidates: list[str],
    client=None,
) -> dict[str, Any] | None:
    prompt = row["messages"][0]["content"]
    snapshot = json.loads(prompt)
    expected = json.loads(row["messages"][1]["content"])
    sample_type = row["sample_type"]
    expected_semantic = expected.get("semantic_judgements", [])
    eval_row = {
        "input_snapshot": snapshot,
        "expected_report": expected,
        "expected_semantic": expected_semantic,
        "sample_type": sample_type,
    }
    scored = []
    for text in candidates:
        pred = extract_json(text)
        metrics = score_sample(pred, eval_row)
        scored.append({"text": text, "metrics": metrics, "key": composite_key(metrics, sample_type)})
    if len(scored) < 2:
        return None
    scored.sort(key=lambda item: item["key"], reverse=True)
    # 硬指标完全同分时，才允许 Judge 在最高分候选之间细排。
    if (
        C.DPO_USE_LLM_JUDGE
        and client is not None
        and len(scored) >= 2
        and scored[0]["key"] == scored[1]["key"]
    ):
        try:
            payload = client.chat_json(
                dpo_judge_prompt(snapshot, expected, [scored[0]["text"], scored[1]["text"]]),
                model=C.JUDGE_MODEL,
                temperature=C.JUDGE_TEMPERATURE,
                max_tokens=C.JUDGE_MAX_TOKENS,
            )
            ranking = payload.get("ranking")
            if isinstance(ranking, list) and ranking[:2] == [1, 0]:
                scored[0], scored[1] = scored[1], scored[0]
        except DeepSeekError:
            pass
    best, worst = scored[0], scored[-1]
    if C.DPO_REQUIRE_HARD_DIFF and not hard_diff(best["metrics"], worst["metrics"], sample_type):
        return None
    return {
        "sample_id": row["sample_id"],
        "sample_type": sample_type,
        "prompt": prompt,
        "chosen": best["text"],
        "rejected": worst["text"],
        "chosen_metrics": best["metrics"],
        "rejected_metrics": worst["metrics"],
        "candidate_count": len(scored),
    }


def split_pairs(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import random

    shuffled = list(rows)
    random.Random(C.DPO_SEED).shuffle(shuffled)
    validation_count = round(len(shuffled) * C.DPO_VALIDATION_RATIO)
    if len(shuffled) >= 2:
        validation_count = max(1, validation_count)
    return shuffled[validation_count:], shuffled[:validation_count]


def to_pair(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt": [{"role": "user", "content": row["prompt"]}],
        "chosen": [{"role": "assistant", "content": row["chosen"]}],
        "rejected": [{"role": "assistant", "content": row["rejected"]}],
        # 让 TRL 内部渲染与 01/05/06/07 推理时一样关闭 thinking。
        "chat_template_kwargs": {"enable_thinking": False},
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    C.ensure_output_dirs()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = args.output_dir / "dpo_candidates.jsonl"
    train_path = args.output_dir / "dpo_train.jsonl"
    validation_path = args.output_dir / "dpo_validation.jsonl"

    prompts = load_prompts(args.sft_data, args.bad_cases)
    if args.max_prompts:
        prompts = prompts[: args.max_prompts]
    if not prompts:
        raise ValueError("没有可用的 SFT 训练样本")

    existing = load_existing_ids(candidates_path) if args.resume else set()
    pending = [row for row in prompts if row["sample_id"] not in existing]
    if args.resume and candidates_path.exists():
        cached = read_jsonl(candidates_path)
    else:
        cached = []
        if candidates_path.exists():
            candidates_path.unlink()

    model, tokenizer, torch_module = load_generation_model(args.model)
    client = get_client() if C.DPO_USE_LLM_JUDGE else None
    for index, row in enumerate(pending, 1):
        candidates = sample_candidates(
            model, tokenizer, torch_module, row["messages"][0]["content"],
            count=args.num_candidates, seed=C.DPO_SEED + index * 100,
        )
        built = build_row(row, candidates, client=client)
        if built is None:
            print(f"[{index}/{len(pending)}] {row['sample_id']} 跳过：候选不足或无硬指标差异")
            continue
        append_jsonl(candidates_path, [built])
        cached.append(built)
        print(f"[{index}/{len(pending)}] {row['sample_id']} 已构造偏好对")

    if len(cached) < 2:
        raise RuntimeError(
            "DPO 偏好对不足 2 条，无法同时构造训练集和验证集；"
            "请增加 train 种子或检查候选质量。"
        )
    train_rows, validation_rows = split_pairs(cached)
    write_jsonl(train_path, [to_pair(row) for row in train_rows])
    write_jsonl(validation_path, [to_pair(row) for row in validation_rows])
    write_json(
        args.output_dir / "dpo_summary.json",
        {
            "source_prompts": len(prompts),
            "pairs": len(cached),
            "train": len(train_rows),
            "validation": len(validation_rows),
            "generation_model": str(args.model),
        },
    )
    print(
        json.dumps(
            {
                "pairs": len(cached),
                "train": len(train_rows),
                "validation": len(validation_rows),
                "train_output": str(train_path),
                "validation_output": str(validation_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
