"""生成 final_test 评估集：输入快照 + 确定性 gold 报告。

final_test 不调用 DeepSeek，gold 全部由规则引擎和种子标注生成，
保证评估基线与训练数据隔离，且 judge 不参与 gold 构造。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from core.io_utils import read_json, read_jsonl, write_jsonl  # noqa: E402
from core.report import (  # noqa: E402
    build_gold_report,
    build_reject_output,
    build_semantic_output,
    validate_reject_output,
    validate_report,
    validate_semantic_output,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成合同审查 final_test 评估集")
    parser.add_argument("--output-dir", type=Path, default=C.FINAL_TEST_DIR)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def load_final_test_seeds() -> list[dict[str, Any]]:
    manifest_path = C.MANIFEST_DIR / "final_test_manifest.json"
    seeds = read_jsonl(C.SEEDS_DIR / "final_test" / "final_test.jsonl")
    if not manifest_path.exists():
        return seeds
    seed_ids = read_json(manifest_path).get("seed_ids") or []
    by_id = {seed["seed_id"]: seed for seed in seeds}
    missing = [seed_id for seed_id in seed_ids if seed_id not in by_id]
    if missing:
        raise ValueError(f"manifest 引用了不存在的种子: {missing[:5]}")
    selected = [by_id[seed_id] for seed_id in seed_ids]
    if any(seed.get("split") != "final_test" for seed in selected):
        raise ValueError("final_test manifest 中存在 split != final_test 的种子")
    return selected


def build_eval_row(seed: dict[str, Any]) -> dict[str, Any]:
    snapshot = seed["input_snapshot"]
    sample_type = seed["sample_type"]
    if sample_type == "review":
        expected = build_gold_report(snapshot, seed)
        errors = validate_report(expected, snapshot, seed)
    elif sample_type == "semantic":
        expected = build_semantic_output(seed)
        errors = validate_semantic_output(expected, snapshot, seed)
    else:
        expected = build_reject_output(snapshot)
        errors = validate_reject_output(expected)
    if errors:
        raise ValueError(f"{seed['seed_id']} final_test gold 非法: {errors[:3]}")
    return {
        "sample_id": seed["seed_id"],
        "seed_id": seed["seed_id"],
        "split": "final_test",
        "category_id": seed["category_id"],
        "category": seed["category"],
        "sample_type": sample_type,
        "contract_type": seed.get("contract_type"),
        "is_scanned": seed.get("is_scanned"),
        "input_snapshot": snapshot,
        "expected_report": expected,
        "expected_semantic": seed.get("expected_semantic", []),
        "required_facts": seed.get("required_facts", []),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    seeds = load_final_test_seeds()
    if not seeds:
        raise ValueError("final_test 没有种子；请先运行 00_build_seeds.py 并确认合同数量足够")
    if args.limit:
        seeds = seeds[: args.limit]
    rows = [build_eval_row(seed) for seed in seeds]

    by_type: dict[str, list[dict[str, Any]]] = {sample_type: [] for sample_type in C.SAMPLE_TYPES}
    for row in rows:
        by_type[row["sample_type"]].append(row)

    for sample_type, payload in by_type.items():
        path = args.output_dir / f"{sample_type}.jsonl"
        write_jsonl(path, payload)
        print(f"{sample_type}: {len(payload)} 条 -> {path}")
    write_jsonl(args.output_dir / "final_test_all.jsonl", rows)

    print(
        json.dumps(
            {
                "total": len(rows),
                "by_type": dict(Counter(row["sample_type"] for row in rows)),
                "by_category": dict(sorted(Counter(row["category_id"] for row in rows).items())),
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
