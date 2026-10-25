"""LLM Judge 评估：摘要/关注点质量，以及 SFT vs DPO 的 pairwise Win Rate。"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from core.deepseek import DeepSeekError, get_client  # noqa: E402
from core.eval_utils import extract_json, generate_text, load_model  # noqa: E402
from core.io_utils import read_jsonl, write_json, write_jsonl  # noqa: E402
from core.prompts import dpo_judge_prompt  # noqa: E402

POINTWISE_PROMPT = """
你是合同审查报告评审员。请依据输入快照和 gold 结论，评价待评报告的自然语言质量。

只评价两项，不重新计算规则：
1. summary_score（1~5）：是否覆盖命中规则数量、整体风险、主要风险方向、复核动作；是否有归纳；是否编造。
2. focus_score（1~5）：关注点是否可执行、是否优先高风险、是否控制在 3~5 条、是否引用原文。

评分标准：5=优秀，4=良好，3=合格，2=明显不足，1=不可用。
只输出 JSON：{"summary_score": 1-5, "focus_score": 1-5, "reason": "一句话理由"}。

## 输入快照
{{SNAPSHOT_JSON}}

## gold 结论
{{GOLD_JSON}}

## 待评报告
{{PRED_JSON}}
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合同审查 LLM Judge 评估")
    parser.add_argument("--model-a", type=Path, default=C.EVAL_MODEL_PATH)
    parser.add_argument("--model-b", type=Path, default=C.EVAL_MODEL_B_PATH)
    parser.add_argument("--data", type=Path, default=C.FINAL_TEST_DIR / "final_test_all.jsonl")
    parser.add_argument("--output-dir", type=Path, default=C.EVAL_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=C.EVAL_LIMIT)
    parser.add_argument("--max-new-tokens", type=int, default=C.EVAL_MAX_NEW_TOKENS)
    return parser.parse_args()


def empty_cuda_cache(torch_module) -> None:
    """模型变量在调用方 del 之后，回收 Python 对象并清空 CUDA 缓存。"""
    gc.collect()
    if torch_module is not None and torch_module.cuda.is_available():
        torch_module.cuda.empty_cache()


def pointwise(client, snapshot: dict, gold: dict, pred: dict) -> dict[str, Any] | None:
    prompt = (
        POINTWISE_PROMPT.replace("{{SNAPSHOT_JSON}}", json.dumps(snapshot, ensure_ascii=False, indent=2))
        .replace("{{GOLD_JSON}}", json.dumps(gold, ensure_ascii=False, indent=2))
        .replace("{{PRED_JSON}}", json.dumps(pred, ensure_ascii=False, indent=2))
    )
    try:
        payload = client.chat_json(
            prompt,
            model=C.JUDGE_MODEL,
            temperature=C.JUDGE_TEMPERATURE,
            max_tokens=C.JUDGE_MAX_TOKENS,
        )
    except DeepSeekError:
        return None
    try:
        summary_score = int(payload["summary_score"])
        focus_score = int(payload["focus_score"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (1 <= summary_score <= 5 and 1 <= focus_score <= 5):
        return None
    return {
        "summary_score": summary_score,
        "focus_score": focus_score,
        "reason": str(payload.get("reason", "")),
    }


def pairwise(client, snapshot: dict, rubric: dict, report_a: str, report_b: str) -> str | None:
    prompt = dpo_judge_prompt(snapshot, rubric, [report_a, report_b])
    try:
        payload = client.chat_json(
            prompt,
            model=C.JUDGE_MODEL,
            temperature=C.JUDGE_TEMPERATURE,
            max_tokens=C.JUDGE_MAX_TOKENS,
        )
    except DeepSeekError:
        return None
    ranking = payload.get("ranking")
    if not isinstance(ranking, list) or len(ranking) < 2 or set(ranking[:2]) != {0, 1}:
        return None
    return "A" if ranking[0] == 0 else "B"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    rows = read_jsonl(args.data)
    # 06 只评价 review 样本的 summary/focus；semantic/reject 由 05 的规则指标覆盖。
    rows = [row for row in rows if row.get("sample_type") == "review"]
    if not rows:
        raise ValueError(f"评估数据里没有 review 样本: {args.data}")
    if args.limit:
        rows = rows[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = get_client()

    # 阶段 A：只加载 SFT 模型，生成完全部答案后立刻释放显存。
    # 这样 pairwise 不会同时驻留两个 8B，避免 device_map="auto" 把参数 offload 到 CPU。
    model_a, tok_a, torch_a = load_model(args.model_a)
    preds_a: list[dict[str, Any] | None] = []
    for index, row in enumerate(rows, 1):
        pred = extract_json(
            generate_text(model_a, tok_a, torch_a, row["input_snapshot"], args.max_new_tokens)
        )
        preds_a.append(pred)
        print(f"[A {index}/{len(rows)}] {row['sample_id']} json={pred is not None}")
    del model_a, tok_a
    empty_cuda_cache(torch_a)

    # 阶段 B：再加载 DPO 模型，生成完全部答案后释放显存。
    preds_b: list[dict[str, Any] | None] = []
    if args.model_b:
        model_b, tok_b, torch_b = load_model(args.model_b)
        for index, row in enumerate(rows, 1):
            pred = extract_json(
                generate_text(model_b, tok_b, torch_b, row["input_snapshot"], args.max_new_tokens)
            )
            preds_b.append(pred)
            print(f"[B {index}/{len(rows)}] {row['sample_id']} json={pred is not None}")
        del model_b, tok_b
        empty_cuda_cache(torch_b)

    # 阶段 C：两个本地模型都已经释放，只保留 DeepSeek Judge 客户端做比较。
    details: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        snapshot = row["input_snapshot"]
        gold = row["expected_report"]
        pred_a = preds_a[index]
        if args.model_b:
            pred_b = preds_b[index]
            winner = None
            if pred_a is not None and pred_b is not None:
                winner = pairwise(
                    client,
                    snapshot,
                    gold,
                    json.dumps(pred_a, ensure_ascii=False),
                    json.dumps(pred_b, ensure_ascii=False),
                )
            details.append(
                {
                    "sample_id": row["sample_id"],
                    "sample_type": row["sample_type"],
                    "winner": winner,
                    "model_a_valid": pred_a is not None,
                    "model_b_valid": pred_b is not None,
                }
            )
            print(f"[judge {index + 1}/{len(rows)}] {row['sample_id']} winner={winner}")
        else:
            score = pointwise(client, snapshot, gold, pred_a) if pred_a is not None else None
            details.append(
                {
                    "sample_id": row["sample_id"],
                    "sample_type": row["sample_type"],
                    "json_valid": pred_a is not None,
                    "score": score,
                }
            )
            print(f"[judge {index + 1}/{len(rows)}] {row['sample_id']} score={score}")

    name_a = args.model_a.name
    if args.model_b:
        valid = [item for item in details if item["winner"] in {"A", "B"}]
        summary = {
            "model_a": str(args.model_a),
            "model_b": str(args.model_b),
            "count": len(details),
            "decided": len(valid),
            "a_wins": sum(1 for item in valid if item["winner"] == "A"),
            "b_wins": sum(1 for item in valid if item["winner"] == "B"),
            "b_win_rate": (
                sum(1 for item in valid if item["winner"] == "B") / len(valid) if valid else None
            ),
        }
        output = args.output_dir / f"judge_{name_a}_vs_{args.model_b.name}.json"
    else:
        scored = [item["score"] for item in details if item["score"]]
        summary = {
            "model": str(args.model_a),
            "count": len(details),
            "scored": len(scored),
            "summary_score": (
                sum(item["summary_score"] for item in scored) / len(scored) if scored else None
            ),
            "focus_score": (
                sum(item["focus_score"] for item in scored) / len(scored) if scored else None
            ),
        }
        output = args.output_dir / f"judge_pointwise_{name_a}.json"

    write_json(output, summary)
    write_jsonl(output.with_suffix(".details.jsonl"), details)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"已写入 {output}")


if __name__ == "__main__":
    main()
