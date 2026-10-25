"""从真实合同（含 00b 生成的变体）构建合同审查种子。

流程：
  data/raw_contracts/*.md + data/variant_contracts/*.md
    -> core.contract_parse.parse_contract
    -> core.rule_engine.run_rules
    -> core.snapshot.build_seeds_from_contracts
    -> data/v2/01_seeds/{train,validation,final_test}/*.jsonl
    -> data/v2/02_manifests/*.json

同一份合同及其变体只会进入一个 split，避免条款泄漏。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from core.io_utils import write_json, write_jsonl  # noqa: E402
from core.rule_engine import load_rules  # noqa: E402
from core.snapshot import (  # noqa: E402
    build_seeds_from_contracts,
    build_variant_parent_map,
    cross_split_clause_overlap,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从真实合同和变体构建 train/validation/final_test 种子")
    parser.add_argument("--raw-dir", type=Path, default=C.RAW_CONTRACTS_DIR)
    parser.add_argument("--variant-dir", type=Path, default=C.VARIANT_CONTRACTS_DIR)
    parser.add_argument("--seeds-dir", type=Path, default=C.SEEDS_DIR)
    parser.add_argument("--manifest-dir", type=Path, default=C.MANIFEST_DIR)
    parser.add_argument("--rules", type=Path, default=C.RULES_PATH)
    return parser.parse_args()


def validate_seed(seed: dict) -> None:
    required = (
        "seed_id", "split", "category_id", "category", "subcategory",
        "scenario", "user_goal", "input_snapshot", "required_facts",
        "expected_output_points", "expected_semantic", "prohibited_actions",
        "output_required", "sample_type",
    )
    missing = [field for field in required if field not in seed]
    if missing:
        raise ValueError(f"种子 {seed.get('seed_id')} 缺少字段 {missing}")
    if seed["sample_type"] not in C.SAMPLE_TYPES:
        raise ValueError(f"种子 {seed['seed_id']} sample_type 非法")
    if seed["split"] not in C.SPLITS:
        raise ValueError(f"种子 {seed['seed_id']} split 非法")
    snapshot = seed["input_snapshot"]
    if seed["sample_type"] in {"review", "semantic"} and not snapshot.get("clauses"):
        raise ValueError(f"种子 {seed['seed_id']} 没有条款")
    if seed["sample_type"] == "reject":
        reject_type = snapshot.get("reject_type")
        if reject_type not in C.REJECT_TYPES:
            raise ValueError(f"reject 种子 {seed['seed_id']} 的 reject_type 非法")
        if reject_type in {"parse_failed", "out_of_scope"} and snapshot.get("clauses"):
            raise ValueError(f"{reject_type} 种子不应包含条款")
        if reject_type in {"insufficient", "semantic_unknown"} and not snapshot.get("clauses"):
            raise ValueError(f"{reject_type} 种子必须包含可判断的条款")
        if reject_type == "semantic_unknown" and not snapshot.get("semantic_candidates"):
            raise ValueError("semantic_unknown 种子必须包含 semantic_candidates")

    snapshot_hits = {item["rule_code"] for item in snapshot.get("rule_hits", [])}
    candidate_codes = {item["rule_code"] for item in snapshot.get("semantic_candidates", [])}
    if not set(seed["required_facts"]) <= (snapshot_hits | candidate_codes):
        raise ValueError(f"种子 {seed['seed_id']} 的 required_facts 不在输入快照内")
    label_codes = {item["rule_code"] for item in seed.get("expected_semantic", [])}
    if not label_codes <= candidate_codes:
        raise ValueError(f"种子 {seed['seed_id']} 的 expected_semantic 不在 semantic_candidates 内")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    raw_paths = sorted(
        path for pattern in ("*.md", "*.txt")
        for path in args.raw_dir.glob(pattern)
    )
    if not raw_paths:
        raise FileNotFoundError(f"没有找到合同文件: {args.raw_dir}/*.md")
    variant_paths = (
        sorted(
            path for pattern in ("*.md", "*.txt")
            for path in args.variant_dir.glob(pattern)
        )
        if args.variant_dir.exists()
        else []
    )
    paths = raw_paths + variant_paths
    variant_parent_map = build_variant_parent_map(raw_paths, variant_paths)

    rules = load_rules(args.rules)
    pools, split_map, parsed_map = build_seeds_from_contracts(
        paths, rules, variant_parent_map=variant_parent_map
    )

    for variant, parent in variant_parent_map.items():
        if split_map.get(variant) != split_map.get(parent):
            raise ValueError(
                f"变体 {variant.name} 与父合同 {parent.name} 不在同一个 split"
            )

    overlaps = cross_split_clause_overlap(split_map, parsed_map)
    if overlaps:
        message = "跨 split 存在相同条款（可能造成泄漏）: " + ", ".join(
            f"{left}<->{right}:{count}" for left, right, count in overlaps
        )
        if C.FAIL_ON_CROSS_SPLIT_OVERLAP:
            raise ValueError(message + "。请删除重复合同或调整切分。")
        print(f"[警告] {message}")

    fingerprint = hashlib.sha1()
    for path in paths:
        fingerprint.update(path.name.encode("utf-8"))
        fingerprint.update(path.read_bytes())
    fingerprint.update(args.rules.read_bytes())

    all_ids: set[str] = set()
    for split in C.SPLITS:
        seeds = pools[split]
        for seed in seeds:
            validate_seed(seed)
            if seed["seed_id"] in all_ids:
                raise ValueError(f"seed_id 重复: {seed['seed_id']}")
            all_ids.add(seed["seed_id"])
        output = args.seeds_dir / split / f"{split}.jsonl"
        write_jsonl(output, seeds)
        manifest = {
            "split": split,
            "seed_ids": [seed["seed_id"] for seed in seeds],
            "target_seed_count": len(seeds),
            "contracts": [
                path.name for path in raw_paths if split_map[path] == split
            ],
            "variant_contracts": [
                path.name for path in variant_paths if split_map[path] == split
            ],
            "fingerprint": fingerprint.hexdigest(),
            "status": "auto_generated",
        }
        write_json(args.manifest_dir / f"{split}_manifest.json", manifest)

    print("合同切分（父合同 + 变体）：")
    for split in C.SPLITS:
        contracts = [path.name for path in raw_paths if split_map[path] == split]
        variants = [path.name for path in variant_paths if split_map[path] == split]
        print(
            f"  {split}: 父合同 {len(contracts)} 份 + 变体 {len(variants)} 份 "
            f"-> {', '.join(contracts + variants) if contracts or variants else '无'}"
        )

    print("\n种子统计：")
    for split in C.SPLITS:
        type_counts = Counter(seed["sample_type"] for seed in pools[split])
        category_counts = Counter(seed["category_id"] for seed in pools[split])
        print(
            f"  {split}: 共 {len(pools[split])} 条 | "
            f"review={type_counts.get('review', 0)} "
            f"semantic={type_counts.get('semantic', 0)} "
            f"reject={type_counts.get('reject', 0)} | "
            f"类别={dict(sorted(category_counts.items()))}"
        )

    empty_splits = [split for split in ("validation", "final_test") if not pools[split]]
    if empty_splits:
        print(
            f"\n[警告] {empty_splits} 没有种子：合同数量太少，"
            "请增加 raw_contracts 后重跑，否则无法做验证/最终评估。"
        )

    print(f"\n已写入 {args.seeds_dir} 和 {args.manifest_dir}")


if __name__ == "__main__":
    main()
