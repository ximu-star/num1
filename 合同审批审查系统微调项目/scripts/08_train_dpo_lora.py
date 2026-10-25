"""从 SFT 合并模型出发，执行 LoRA DPO 训练。

与老师 08 的差异：
- prompt 允许 1~2 条消息（本项目实际是 1 条 user，内容是完整 input_snapshot）；
- max_prompt_length 4096 / max_completion_length 2048；
- 训练前打印 prompt/completion 的 token 长度分布，便于发现截断。
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
    parser = argparse.ArgumentParser(description="合同审查 LoRA DPO 训练")
    parser.add_argument("--data", type=Path, default=C.DPO_DATA_DIR / "dpo_train.jsonl")
    parser.add_argument("--eval-data", type=Path, default=C.DPO_DATA_DIR / "dpo_validation.jsonl")
    parser.add_argument("--model", type=Path, default=C.DPO_GENERATION_MODEL)
    parser.add_argument("--chat-template", type=Path, default=C.CHAT_TEMPLATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=C.DPO_OUTPUT_DIR)
    parser.add_argument("--logging-dir", type=Path, default=C.DPO_LOG_DIR)
    parser.add_argument("--epochs", type=float, default=C.DPO_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=C.DPO_BATCH_SIZE)
    parser.add_argument("--eval-batch-size", type=int, default=C.DPO_EVAL_BATCH_SIZE)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=C.DPO_GRADIENT_ACCUMULATION_STEPS)
    parser.add_argument("--learning-rate", type=float, default=C.DPO_LEARNING_RATE)
    parser.add_argument("--beta", type=float, default=C.DPO_BETA)
    parser.add_argument("--max-prompt-length", type=int, default=C.DPO_MAX_PROMPT_LENGTH)
    parser.add_argument("--max-completion-length", type=int, default=C.DPO_MAX_COMPLETION_LENGTH)
    parser.add_argument("--logging-steps", type=int, default=C.DPO_LOGGING_STEPS)
    parser.add_argument("--save-steps", type=int, default=C.DPO_SAVE_STEPS)
    parser.add_argument("--eval-steps", type=int, default=C.DPO_EVAL_STEPS)
    parser.add_argument("--seed", type=int, default=C.DPO_SEED)
    parser.add_argument("--dry-run", action="store_true", help="只校验数据和长度，不加载模型")
    parser.add_argument("--dry-run-trainer", action="store_true", help="构造 Trainer 并检查 DataLoader")
    return parser.parse_args()


def validate_message_list(messages: Any, expected_role: str, field_name: str, line_number: int) -> None:
    if not isinstance(messages, list) or not (1 <= len(messages) <= 2):
        raise ValueError(f"第 {line_number} 行 {field_name} 必须包含 1~2 条消息")
    if messages[-1].get("role") != expected_role:
        raise ValueError(f"第 {line_number} 行 {field_name} 最后一条消息角色必须是 {expected_role}")
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError(f"第 {line_number} 行 {field_name} 消息必须是对象")
        if message.get("role") not in {"system", "user", "assistant"}:
            raise ValueError(f"第 {line_number} 行 {field_name} 角色非法")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"第 {line_number} 行 {field_name}.content 必须是非空字符串")


def load_dpo_samples(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"DPO 数据不存在: {path}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        validate_message_list(row.get("prompt"), "user", "prompt", line_number)
        validate_message_list(row.get("chosen"), "assistant", "chosen", line_number)
        validate_message_list(row.get("rejected"), "assistant", "rejected", line_number)
        if row["chosen"][-1]["content"].strip() == row["rejected"][-1]["content"].strip():
            raise ValueError(f"第 {line_number} 行 chosen 与 rejected 不能相同")
        prompt_key = row["prompt"][-1]["content"].strip()
        if prompt_key in seen:
            raise ValueError(f"第 {line_number} 行 prompt 重复")
        seen.add(prompt_key)
        rows.append(row)
    if not rows:
        raise ValueError(f"DPO 数据为空: {path}")
    return rows


def ensure_disjoint_prompts(train_rows, eval_rows) -> None:
    overlap = sorted(
        {row["prompt"][-1]["content"].strip() for row in train_rows}
        & {row["prompt"][-1]["content"].strip() for row in eval_rows}
    )
    if overlap:
        raise ValueError(f"DPO 训练与验证集存在重复 prompt: {overlap[:3]}")


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


def report_lengths(tokenizer, rows, max_prompt: int, max_completion: int) -> dict[str, Any]:
    prompt_lengths = []
    completion_lengths = []
    for row in rows:
        encoded = tokenizer.apply_chat_template(
            row["prompt"],
            tokenize=True,
            return_dict=True,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        prompt_lengths.append(len(encoded["input_ids"]))
        completion_lengths.append(len(tokenizer(row["chosen"][-1]["content"], add_special_tokens=False).input_ids))
    return {
        "count": len(rows),
        "prompt_max": max(prompt_lengths) if prompt_lengths else 0,
        "prompt_avg": round(sum(prompt_lengths) / len(prompt_lengths)) if prompt_lengths else 0,
        "prompt_over": sum(1 for length in prompt_lengths if length > max_prompt),
        "completion_max": max(completion_lengths) if completion_lengths else 0,
        "completion_avg": round(sum(completion_lengths) / len(completion_lengths)) if completion_lengths else 0,
        "completion_over": sum(1 for length in completion_lengths if length > max_completion),
        "max_prompt_length": max_prompt,
        "max_completion_length": max_completion,
    }


def build_peft_config():
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
    if importlib.util.find_spec("tensorboard") or importlib.util.find_spec("tensorboardX"):
        return "tensorboard"
    print("[提示] 未安装 tensorboard，本次训练不写 TensorBoard 日志。")
    return "none"


def build_training_args(args, use_bf16: bool, use_fp16: bool, use_cpu: bool):
    from trl import DPOConfig

    return DPOConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        beta=args.beta,
        max_prompt_length=args.max_prompt_length,
        max_completion_length=args.max_completion_length,
        logging_steps=args.logging_steps,
        logging_first_step=True,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=C.DPO_SAVE_TOTAL_LIMIT,
        bf16=use_bf16,
        fp16=use_fp16,
        use_cpu=use_cpu,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to=_report_to(),
        logging_dir=str(args.logging_dir),
        seed=args.seed,
    )


def build_trainer(args, tokenizer, train_rows, eval_rows, require_cuda: bool):
    import torch
    from transformers import AutoModelForCausalLM
    from trl import DPOTrainer

    use_cuda = torch.cuda.is_available()
    if require_cuda and not use_cuda:
        raise RuntimeError("未检测到 CUDA GPU；DPO 训练需要在 GPU 环境运行")
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
    model.config.use_cache = False
    # transformers 5.x 移除了 warnings_issued，而 TRL 0.24 的 DPOTrainer 仍会写入。
    if not hasattr(model, "warnings_issued"):
        model.warnings_issued = {}
    from datasets import Dataset

    def with_template_kwargs(rows):
        return [
            {**row, "chat_template_kwargs": {"enable_thinking": False}}
            for row in rows
        ]

    return DPOTrainer(
        model=model,
        ref_model=None,
        args=build_training_args(args, use_bf16, use_fp16, use_cpu=not use_cuda),
        train_dataset=Dataset.from_list(with_template_kwargs(train_rows)),
        eval_dataset=Dataset.from_list(with_template_kwargs(eval_rows)),
        processing_class=tokenizer,
        peft_config=build_peft_config(),
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    C.ensure_output_dirs()
    train_rows = load_dpo_samples(args.data)
    eval_rows = load_dpo_samples(args.eval_data)
    ensure_disjoint_prompts(train_rows, eval_rows)
    tokenizer = load_tokenizer(args.model, args.chat_template)
    print(f"DPO 训练样本: {len(train_rows)} 条 | 验证样本: {len(eval_rows)} 条")
    print("训练集长度:", report_lengths(tokenizer, train_rows, args.max_prompt_length, args.max_completion_length))
    print("验证集长度:", report_lengths(tokenizer, eval_rows, args.max_prompt_length, args.max_completion_length))

    if args.dry_run or args.dry_run_trainer:
        if args.dry_run_trainer:
            trainer = build_trainer(args, tokenizer, train_rows[:1], eval_rows[:1], require_cuda=False)
            next(iter(trainer.get_train_dataloader()))
            next(iter(trainer.get_eval_dataloader()))
        print("\nDry-run 完成：数据格式和长度已检查。")
        return

    trainer = build_trainer(args, tokenizer, train_rows, eval_rows, require_cuda=True)
    trainer.train()
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(args.output_dir)
    print(f"DPO Adapter 已保存: {args.output_dir}")


if __name__ == "__main__":
    main()
