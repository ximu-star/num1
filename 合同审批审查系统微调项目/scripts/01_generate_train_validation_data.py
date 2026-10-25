"""基于合同审查种子，生成 train / validation 的 SFT 数据。

与老师 01 的差异：
- 不再生成“用户对话”，而是把 seed.input_snapshot 作为 user，把审查报告 JSON 作为 assistant；
- 结构化结论由规则引擎 + gold 锁定，DeepSeek 只负责摘要/关注点/语义理由的措辞；
- 不再使用 tools 字段，避免 Qwen 模板走 function-calling 分支。
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
from core.io_utils import (  # noqa: E402
    append_jsonl,
    compact_json,
    read_json,
    read_jsonl,
)
from core.prompts import review_prompt, semantic_prompt  # noqa: E402
from core.report import (  # noqa: E402
    assistant_content,
    build_gold_report,
    build_reject_output,
    build_semantic_output,
    validate_narrative,
    validate_reject_output,
    validate_report,
    validate_semantic_output,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成合同审查 SFT 训练/验证数据")
    parser.add_argument("--split", choices=["train", "validation"], default="train")
    parser.add_argument("--variants-per-seed", type=int, default=C.VARIANTS_PER_SEED)
    parser.add_argument("--count", type=int, default=None, help="最多生成多少条样本")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="不调用 API，用模板文本构造样本")
    return parser.parse_args()


def load_split_seeds(split: str) -> list[dict[str, Any]]:
    manifest_path = C.MANIFEST_DIR / f"{split}_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        seed_ids = manifest.get("seed_ids") or []
    else:
        seed_ids = []
    seeds = read_jsonl(C.SEEDS_DIR / split / f"{split}.jsonl")
    if seed_ids:
        by_id = {seed["seed_id"]: seed for seed in seeds}
        missing = [seed_id for seed_id in seed_ids if seed_id not in by_id]
        if missing:
            raise ValueError(f"manifest 引用了不存在的种子: {missing[:5]}")
        seeds = [by_id[seed_id] for seed_id in seed_ids]
    seed_types = {seed["sample_type"] for seed in seeds}
    if not seed_types:
        raise ValueError(f"{split} 没有种子")
    unknown = seed_types - set(C.SAMPLE_TYPES)
    if unknown:
        raise ValueError(f"未知 sample_type: {sorted(unknown)}")
    if any(seed.get("split") != split for seed in seeds):
        raise ValueError(f"{split} 种子里存在 split 字段不一致的样本")
    return seeds


def build_plan(
    seeds: list[dict[str, Any]],
    variants: int,
    count: int | None,
    *,
    semantic_variants: int | None = None,
    reject_variants: int = 1,
) -> list[dict[str, Any]]:
    """按样本类型生成计划。

    语义样本是当前最薄弱的能力，默认多生成几个变体；
    拒答输出是确定性的，重复变体没有意义，默认 1 条。
    """
    semantic_variants = semantic_variants or variants
    if min(variants, semantic_variants, reject_variants) <= 0:
        raise ValueError("variants 必须大于 0")
    plan: list[dict[str, Any]] = []
    for seed in seeds:
        if seed["sample_type"] == "semantic":
            seed_variants = semantic_variants
        elif seed["sample_type"] == "reject":
            seed_variants = reject_variants
        else:
            seed_variants = variants
        for variant in range(1, seed_variants + 1):
            plan.append(
                {
                    "sample_id": f"{seed['seed_id']}__{seed['sample_type']}_{variant:03d}",
                    "seed": seed,
                    "variant": variant,
                }
            )
    return plan[:count] if count else plan


def _narrative_with_retries(client, prompt: str, snapshot: dict, seed: dict, gold: dict) -> dict | None:
    errors: list[str] = []
    for attempt in range(1, C.NARRATIVE_RETRIES + 1):
        try:
            payload = client.chat_json(prompt)
        except DeepSeekError as exc:
            errors = [str(exc)]
            continue
        errors = validate_narrative(payload, snapshot, seed, gold)
        if not errors:
            return payload
        print(f"  [警告] 第 {attempt} 次自然语言校验失败: {errors[:2]}")
    return None


def _semantic_reasons_with_retries(
    client, prompt: str, snapshot: dict, seed: dict
) -> dict[str, str] | None:
    for attempt in range(1, C.NARRATIVE_RETRIES + 1):
        try:
            payload = client.chat_json(prompt)
        except DeepSeekError as exc:
            print(f"  [警告] DeepSeek 调用失败: {exc}")
            continue
        reasons = payload.get("semantic_reasons")
        if not isinstance(reasons, dict):
            print("  [警告] semantic_reasons 不是对象")
            continue
        output = build_semantic_output(seed, {k: str(v) for k, v in reasons.items() if isinstance(v, str)})
        errors = validate_semantic_output(output, snapshot, seed)
        if not errors:
            return {item["rule_code"]: item["reason"] for item in output["semantic_judgements"]}
        print(f"  [警告] 第 {attempt} 次语义理由校验失败: {errors[:2]}")
    return None


def make_sample(
    seed: dict[str, Any],
    *,
    use_llm: bool,
    client=None,
) -> dict[str, Any]:
    snapshot = seed["input_snapshot"]
    sample_type = seed["sample_type"]
    narrative_source = "template"

    if sample_type == "review":
        gold = build_gold_report(snapshot, seed)
        narrative = None
        if use_llm:
            # 不把模板 summary/focus 喂给模型，避免它直接抄模板；只给结构化结论。
            prompt_gold = {
                "overall_risk_level": gold["overall_risk_level"],
                "hit_rules": gold["hit_rules"],
                "semantic_judgements": gold["semantic_judgements"],
            }
            narrative = _narrative_with_retries(
                client, review_prompt(snapshot, prompt_gold, seed), snapshot, seed, gold
            )
            narrative_source = "llm" if narrative is not None else "template"
        result = build_gold_report(snapshot, seed, narrative)
        errors = validate_report(result, snapshot, seed)
        if errors:
            raise ValueError(f"{seed['seed_id']} gold 报告非法: {errors[:3]}")
    elif sample_type == "semantic":
        result = build_semantic_output(seed)
        if use_llm:
            reasons = _semantic_reasons_with_retries(
                client, semantic_prompt(snapshot, result, seed), snapshot, seed
            )
            if reasons:
                candidate = build_semantic_output(seed, reasons)
                if not validate_semantic_output(candidate, snapshot, seed):
                    result = candidate
                    narrative_source = "llm"
        errors = validate_semantic_output(result, snapshot, seed)
        if errors:
            raise ValueError(f"{seed['seed_id']} semantic gold 非法: {errors[:3]}")
    else:
        result = build_reject_output(snapshot)
        errors = validate_reject_output(result)
        if errors:
            raise ValueError(f"{seed['seed_id']} reject gold 非法: {errors[:3]}")

    return {
        "sample_id": "",  # 由调用方填充
        "seed_id": seed["seed_id"],
        "split": seed["split"],
        "category_id": seed["category_id"],
        "category": seed["category"],
        "sample_type": sample_type,
        "generation_mode": "llm" if use_llm else "template",
        "narrative_source": narrative_source,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "user", "content": compact_json(snapshot)},
            {"role": "assistant", "content": assistant_content(result)},
        ],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    C.ensure_output_dirs()

    use_llm = C.USE_LLM_NARRATIVE and not args.dry_run
    variants = args.variants_per_seed
    if not use_llm and variants > 1:
        print("提示: 未启用 LLM 润色，variants 自动降为 1，避免重复样本。")
        variants = 1

    if args.output is not None:
        output = args.output
    elif args.dry_run:
        output = C.SFT_DATA_DIR / "smoke" / f"sft_{args.split}.dryrun.jsonl"
    else:
        output = C.SFT_DATA_DIR / f"sft_{args.split}.jsonl"
    output = output if output.is_absolute() else ROOT / output
    seeds = load_split_seeds(args.split)
    semantic_variants = C.SEMANTIC_VARIANTS_PER_SEED if use_llm else 1
    reject_variants = C.REJECT_VARIANTS_PER_SEED if use_llm else 1
    plan = build_plan(
        seeds,
        variants,
        args.count,
        semantic_variants=semantic_variants,
        reject_variants=reject_variants,
    )

    if output.exists() and not args.resume:
        raise FileExistsError(f"输出已存在；如需补齐请加 --resume: {output}")
    existing_rows = read_jsonl(output) if args.resume and output.exists() else []
    existing_ids = [row.get("sample_id") for row in existing_rows]
    if any(not isinstance(value, str) or not value for value in existing_ids):
        raise ValueError("断点文件存在无效 sample_id")
    if len(existing_ids) != len(set(existing_ids)):
        raise ValueError("断点文件存在重复 sample_id")
    plan_seed_ids = {
        entry["seed"]["seed_id"]
        for entry in build_plan(
            seeds,
            variants,
            None,
            semantic_variants=semantic_variants,
            reject_variants=reject_variants,
        )
    }
    stale = [row["sample_id"] for row in existing_rows if row.get("seed_id") not in plan_seed_ids]
    if stale:
        raise ValueError(
            f"断点文件有 {len(stale)} 条样本不属于当前 {args.split} split；"
            "通常是重跑 00 后合同切分变化，请删除旧 SFT 数据并重新生成。"
        )
    expected_mode = "llm" if use_llm else "template"
    mismatched = [
        row.get("sample_id")
        for row in existing_rows
        if row.get("generation_mode") != expected_mode
    ]
    if mismatched:
        raise ValueError(
            f"断点文件里有 {len(mismatched)} 条 generation_mode != {expected_mode} 的样本，"
            "不能混用模板数据和 LLM 数据；请换一个输出路径或删除旧文件。"
        )
    existing = {row["sample_id"] for row in existing_rows}
    pending = [entry for entry in plan if entry["sample_id"] not in existing]
    if not pending:
        print(f"{args.split}: 计划 {len(plan)} 条，已全部存在: {output}")
        return

    client = get_client() if use_llm else None
    generated = 0
    for entry in pending:
        seed = entry["seed"]
        sample = make_sample(seed, use_llm=use_llm, client=client)
        sample["sample_id"] = entry["sample_id"]
        append_jsonl(output, [sample])
        generated += 1
        print(
            f"[{args.split}] {generated}/{len(pending)} "
            f"{sample['sample_id']} type={sample['sample_type']} "
            f"hits={len(seed['input_snapshot'].get('rule_hits', []))}"
        )

    print(
        json.dumps(
            {
                "split": args.split,
                "target": len(plan),
                "existing": len(existing),
                "generated": generated,
                "output": str(output),
                "llm_narrative": use_llm,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
