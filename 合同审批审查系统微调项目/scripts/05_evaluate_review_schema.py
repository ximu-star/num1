"""规则化评估：JSON 合法率、规则 P/R/F1、证据溯源、风险等级、编造率、语义/拒绝边界。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from core.eval_utils import aggregate, extract_json, generate_text, load_model, score_sample  # noqa: E402
from core.io_utils import read_jsonl, write_json, write_jsonl  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合同审查 SFT/DPO 规则化评估")
    parser.add_argument("--model", type=Path, default=C.EVAL_MODEL_PATH)
    parser.add_argument("--data", type=Path, default=C.FINAL_TEST_DIR / "final_test_all.jsonl")
    parser.add_argument("--output-dir", type=Path, default=C.EVAL_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=C.EVAL_LIMIT)
    parser.add_argument("--max-new-tokens", type=int, default=C.EVAL_MAX_NEW_TOKENS)
    parser.add_argument("--name", default=None, help="结果文件前缀，默认用模型目录名")
    return parser.parse_args()


def group_aggregate(details: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in details:
        grouped[str(item[key])].append(item["metrics"])
    return {name: aggregate(items) for name, items in sorted(grouped.items())}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    rows = read_jsonl(args.data)
    if not rows:
        raise ValueError(f"评估数据为空: {args.data}")
    if args.limit:
        rows = rows[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or args.model.name

    model, tokenizer, torch_module = load_model(args.model)
    details: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        raw = generate_text(model, tokenizer, torch_module, row["input_snapshot"], args.max_new_tokens)
        pred = extract_json(raw)
        try:
            metrics = score_sample(pred, row)
        except Exception as exc:  # 单条畸形输出不能中断整轮评估
            metrics = {"json_valid": pred is not None, "schema_valid": False, "error": str(exc)}
        details.append(
            {
                "sample_id": row["sample_id"],
                "sample_type": row["sample_type"],
                "category_id": row["category_id"],
                "raw_output": raw,
                "prediction": pred,
                "expected": row["expected_report"],
                "metrics": metrics,
            }
        )
        print(
            f"[{index}/{len(rows)}] {row['sample_id']} "
            f"json={metrics['json_valid']} schema={metrics['schema_valid']} "
            f"f1={metrics.get('rule_f1')} evidence={metrics.get('evidence_rate')}"
        )

    summary = {
        "model": str(args.model),
        "data": str(args.data),
        "overall": aggregate([item["metrics"] for item in details]),
        "by_sample_type": group_aggregate(details, "sample_type"),
        "by_category": group_aggregate(details, "category_id"),
        "targets": {
            "json_valid_rate": C.TARGET_JSON_VALID_RATE,
            "evidence_rate": C.TARGET_EVIDENCE_RATE,
            "fabrication_rate": C.TARGET_FABRICATION_RATE,
        },
    }
    write_json(args.output_dir / f"schema_{name}.json", summary)
    write_jsonl(args.output_dir / f"schema_{name}_details.jsonl", details)
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    print(f"已写入 {args.output_dir / f'schema_{name}.json'}")


if __name__ == "__main__":
    main()
