"""合同审查 SFT 训练（LoRA / 全参）。

与老师 03 的差异：
- sample_type 只允许 review / semantic / reject；
- 不再读取或传递 tools 字段；
- assistant_only_loss 仍然为 True，但只校验报告 JSON 进入 mask；
- max_length 默认 6144（真实合同输入+输出实测上界约 6356 BERT token）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="训练合同审查 Qwen3 SFT 模型")
    parser.add_argument("--data", type=Path, default=C.SFT_DATA_DIR / "sft_train.jsonl")
    parser.add_argument("--eval-data", type=Path, default=C.SFT_DATA_DIR / "sft_validation.jsonl")
    parser.add_argument("--model", type=Path, default=C.MODEL_PATH)
    parser.add_argument("--chat-template", type=Path, default=C.CHAT_TEMPLATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=C.SFT_OUTPUT_DIR)
    parser.add_argument("--logging-dir", type=Path, default=C.SFT_LOG_DIR)
    parser.add_argument("--use-lora", action="store_true", default=C.USE_LORA)
    parser.add_argument("--no-lora", action="store_true", help="关闭 LoRA，改为全参数微调")
    parser.add_argument("--epochs", type=float, default=C.SFT_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=C.SFT_BATCH_SIZE)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=C.SFT_GRADIENT_ACCUMULATION_STEPS)
    parser.add_argument("--learning-rate", type=float, default=None)
    parser.add_argument("--max-length", type=int, default=C.SFT_MAX_LENGTH)
    parser.add_argument("--logging-steps", type=int, default=C.SFT_LOGGING_STEPS)
    parser.add_argument("--save-steps", type=int, default=C.SFT_SAVE_STEPS)
    parser.add_argument("--eval-steps", type=int, default=C.SFT_EVAL_STEPS)
    parser.add_argument("--seed", type=int, default=C.SFT_SEED)
    parser.add_argument("--dry-run", action="store_true", help="只校验数据和 token 长度，不加载模型")
    parser.add_argument("--dry-run-trainer", action="store_true", help="额外构造 Trainer 并检查 DataLoader")
    return parser.parse_args()


def load_training_samples(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"训练数据不存在: {path}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        sample_id = row.get("sample_id")
        sample_type = row.get("sample_type")
        messages = row.get("messages")
        if not isinstance(sample_id, str) or not sample_id or sample_id in seen:
            raise ValueError(f"第 {line_number} 行 sample_id 缺失或重复")
        if sample_type not in C.SAMPLE_TYPES:
            raise ValueError(f"第 {line_number} 行 sample_type 无效: {sample_type!r}")
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError(f"第 {line_number} 行 messages 必须是 user/assistant 两条")
        if [message.get("role") for message in messages if isinstance(message, dict)] != ["user", "assistant"]:
            raise ValueError(f"第 {line_number} 行 messages 角色必须是 user -> assistant")
        assistant = messages[1].get("content")
        if not isinstance(assistant, str) or not assistant.strip():
            raise ValueError(f"第 {line_number} 行 assistant 内容为空")
        json.loads(assistant)  # 必须是合法 JSON
        if "tools" in row:
            raise ValueError(f"第 {line_number} 行不应包含 tools 字段")
        seen.add(sample_id)
        rows.append(row)
    if not rows:
        raise ValueError("训练数据为空")
    return rows


def ensure_disjoint_splits(train_rows: list[dict[str, Any]], eval_rows: list[dict[str, Any]]) -> None:
    overlap = sorted({row["sample_id"] for row in train_rows} & {row["sample_id"] for row in eval_rows})
    if overlap:
        raise ValueError(f"训练集和验证集存在重复 sample_id: {overlap[:5]}")


def load_tokenizer(model_path: Path, template_path: Path):
    from transformers import AutoTokenizer

    if not model_path.is_dir():
        raise FileNotFoundError(f"找不到模型目录: {model_path}")
    if not template_path.is_file():
        raise FileNotFoundError(f"找不到聊天模板: {template_path}")
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    tokenizer.chat_template = template_path.read_text(encoding="utf-8")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def render_sample(tokenizer, row: dict[str, Any]) -> str:
    return tokenizer.apply_chat_template(
        row["messages"],
        tokenize=False,
        add_generation_prompt=False,
        enable_thinking=False,
    )


def _normalize_ws(text: str) -> str:
    return "".join(text.split())


def validate_assistant_masks(tokenizer, rows: list[dict[str, Any]]) -> dict[str, int]:
    summary = {sample_type: 0 for sample_type in C.SAMPLE_TYPES}
    summary["checked"] = 0
    for row in rows:
        encoded = tokenizer.apply_chat_template(
            row["messages"],
            tokenize=True,
            return_dict=True,
            return_assistant_tokens_mask=True,
            add_generation_prompt=False,
            enable_thinking=False,
        )
        mask = encoded.get("assistant_masks")
        if mask is None or not any(mask):
            raise ValueError(f"样本 {row['sample_id']} 没有可训练的 assistant token")
        trained_text = tokenizer.decode(
            [token_id for token_id, selected in zip(encoded["input_ids"], mask) if selected],
            skip_special_tokens=False,
        )
        assistant = row["messages"][-1]["content"]
        if _normalize_ws(assistant) not in _normalize_ws(trained_text):
            raise ValueError(f"样本 {row['sample_id']} 的 assistant JSON 未进入 mask")
        summary["checked"] += 1
        summary[row["sample_type"]] += 1
    return summary


def report_length_stats(tokenizer, rows: list[dict[str, Any]], max_length: int) -> dict[str, int]:
    lengths = []
    for row in rows:
        encoded = tokenizer.apply_chat_template(
            row["messages"],
            tokenize=True,
            return_dict=True,
            add_generation_prompt=False,
            enable_thinking=False,
        )
        lengths.append(len(encoded["input_ids"]))
    oversize = [length for length in lengths if length > max_length]
    if oversize and C.SFT_FAIL_ON_OVERSIZE:
        raise ValueError(
            f"{len(oversize)} 条样本超过 max_length={max_length}，"
            f"最大 {max(lengths)}。请减小 MAX_SNAPSHOT_CLAUSES/MAX_CLAUSE_CHARS 或提高 SFT_MAX_LENGTH。"
        )
    return {
        "count": len(lengths),
        "max": max(lengths) if lengths else 0,
        "avg": round(sum(lengths) / len(lengths)) if lengths else 0,
        "oversize": len(oversize),
        "max_length": max_length,
    }


def print_sample_examples(tokenizer, rows: list[dict[str, Any]]) -> None:
    for sample_type in C.SAMPLE_TYPES:
        row = next((item for item in rows if item["sample_type"] == sample_type), None)
        if row is None:
            continue
        print(f"\n===== {sample_type} 示例: {row['sample_id']} =====")
        print(render_sample(tokenizer, row)[:4000])


def build_peft_config(use_lora: bool):
    if not use_lora:
        return None
    from peft import LoraConfig

    return LoraConfig(
        r=C.LORA_R,
        lora_alpha=C.LORA_ALPHA,
        lora_dropout=C.LORA_DROPOUT,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=list(C.LORA_TARGET_MODULES),
    )


def _report_to() -> str:
    """没装 tensorboard 时退回 none，避免 Trainer 初始化直接失败。"""
    if importlib.util.find_spec("tensorboard") or importlib.util.find_spec("tensorboardX"):
        return "tensorboard"
    print("[提示] 未安装 tensorboard，本次训练不写 TensorBoard 日志。")
    return "none"


def build_training_args(args: argparse.Namespace, use_bf16: bool, use_fp16: bool, use_cpu: bool, has_eval: bool):
    from trl import SFTConfig

    learning_rate = args.learning_rate
    if learning_rate is None:
        learning_rate = C.SFT_LEARNING_RATE if args.use_lora else C.SFT_FULL_LEARNING_RATE
    return SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=C.SFT_EVAL_BATCH_SIZE,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=learning_rate,
        max_length=args.max_length,
        logging_steps=args.logging_steps,
        logging_first_step=True,
        eval_strategy="steps" if has_eval else "no",
        eval_steps=args.eval_steps if has_eval else None,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=C.SFT_SAVE_TOTAL_LIMIT,
        bf16=use_bf16,
        fp16=use_fp16,
        use_cpu=use_cpu,
        gradient_checkpointing=True,
        assistant_only_loss=True,
        dataset_num_proc=1,
        report_to=_report_to(),
        logging_dir=str(args.logging_dir),
        seed=args.seed,
    )


def build_train_dataset(rows: list[dict[str, Any]]):
    from datasets import Dataset

    # 让 TRL 内部渲染也使用 enable_thinking=False，与 01/05/06/07 保持一致。
    normalized = [
        {**row, "chat_template_kwargs": {"enable_thinking": False}}
        for row in rows
    ]
    return Dataset.from_list(normalized)


def select_dry_run_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for sample_type in C.SAMPLE_TYPES:
        row = next((item for item in rows if item["sample_type"] == sample_type), None)
        if row is not None:
            selected.append(row)
    return selected


def build_trainer(args: argparse.Namespace, tokenizer, train_rows, eval_rows, require_cuda: bool):
    import torch
    from transformers import AutoModelForCausalLM
    from trl import SFTTrainer

    use_cuda = torch.cuda.is_available()
    if require_cuda and not use_cuda:
        raise RuntimeError("未检测到 CUDA GPU；实际训练需要在 GPU 环境运行")
    use_bf16 = use_cuda and torch.cuda.is_bf16_supported()
    use_fp16 = use_cuda and not use_bf16
    dtype = torch.bfloat16 if use_bf16 else torch.float16 if use_fp16 else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        local_files_only=True,
        dtype=dtype,
        use_cache=False,
        low_cpu_mem_usage=True,
    )
    return SFTTrainer(
        model=model,
        args=build_training_args(
            args, use_bf16, use_fp16, use_cpu=not use_cuda, has_eval=eval_rows is not None
        ),
        train_dataset=build_train_dataset(train_rows),
        eval_dataset=build_train_dataset(eval_rows) if eval_rows is not None else None,
        processing_class=tokenizer,
        peft_config=build_peft_config(args.use_lora),
    )


def detect_sample_type(text: str) -> str:
    if '"overall_risk_level"' in text:
        return "review"
    if '"semantic_judgements"' in text:
        return "semantic"
    return "reject"


def print_dataloader_examples(dataloader, tokenizer, split_name: str) -> None:
    batch = next(iter(dataloader))
    input_ids = batch["input_ids"].detach().cpu()
    labels = batch["labels"].detach().cpu()
    attention_mask = batch.get("attention_mask")
    if attention_mask is not None:
        attention_mask = attention_mask.detach().cpu()
    print(f"\n{split_name} DataLoader batch_size={int(input_ids.shape[0])}")
    for index in range(int(input_ids.shape[0])):
        visible_ids = input_ids[index] if attention_mask is None else input_ids[index][attention_mask[index].bool()]
        trained_ids = labels[index][labels[index] != -100]
        input_text = tokenizer.decode(visible_ids.tolist(), skip_special_tokens=False)
        trained_text = tokenizer.decode(trained_ids.tolist(), skip_special_tokens=False)
        print(f"\n===== {split_name} {detect_sample_type(trained_text)} 样本 =====")
        print("[input]")
        print(input_text)
        print("[labels]")
        print(trained_text)


def train(args, tokenizer, train_rows, eval_rows) -> None:
    trainer = build_trainer(args, tokenizer, train_rows, eval_rows, require_cuda=True)
    trainer.train()
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(args.output_dir)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.no_lora:
        args.use_lora = False
    C.ensure_output_dirs()

    train_rows = load_training_samples(args.data)
    eval_rows = load_training_samples(args.eval_data) if args.eval_data is not None else None
    if eval_rows is not None:
        ensure_disjoint_splits(train_rows, eval_rows)
    tokenizer = load_tokenizer(args.model, args.chat_template)

    train_masks = validate_assistant_masks(tokenizer, train_rows)
    print(f"训练样本: {len(train_rows)} 条 | mask 校验: {train_masks}")
    if eval_rows is not None:
        eval_masks = validate_assistant_masks(tokenizer, eval_rows)
        print(f"验证样本: {len(eval_rows)} 条 | mask 校验: {eval_masks}")

    train_lengths = report_length_stats(tokenizer, train_rows, args.max_length)
    print(f"训练集 token 长度: {train_lengths}")
    if eval_rows is not None:
        print(f"验证集 token 长度: {report_length_stats(tokenizer, eval_rows, args.max_length)}")

    print(f"训练方式: {'LoRA' if args.use_lora else '全参数微调'} | max_length={args.max_length}")
    print_sample_examples(tokenizer, train_rows)

    if args.dry_run or args.dry_run_trainer:
        if args.dry_run_trainer:
            trainer = build_trainer(args, tokenizer, select_dry_run_samples(train_rows), None, require_cuda=False)
            print_dataloader_examples(trainer.get_train_dataloader(), tokenizer, "训练集")
        print("\nDry-run 完成：数据、mask、token 长度和样本渲染已检查。")
        return

    train(args, tokenizer, train_rows, eval_rows)


if __name__ == "__main__":
    main()
