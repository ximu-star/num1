"""把 SFT LoRA Adapter 合并回基座模型，保存完整模型与 tokenizer。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合并 SFT LoRA Adapter")
    parser.add_argument("--base_model", type=Path, default=C.MODEL_PATH)
    # 保留老师原脚本的拼写，同时兼容正确拼写。
    parser.add_argument(
        "--adapater_model",
        "--adapter_model",
        dest="adapater_model",
        type=Path,
        default=C.SFT_OUTPUT_DIR,
    )
    parser.add_argument("--merged_model", type=Path, default=C.MERGED_OUTPUT_DIR)
    parser.add_argument("--device", default=C.MERGE_DEVICE)
    parser.add_argument("--dtype", default=C.MERGE_DTYPE, choices=["bfloat16", "float16", "float32"])
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not args.adapater_model.is_dir():
        raise FileNotFoundError(f"找不到 LoRA Adapter: {args.adapater_model}")
    dtype = {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }[args.dtype]

    print(f"加载基座: {args.base_model}")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        local_files_only=True,
        dtype=dtype,
        low_cpu_mem_usage=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(args.adapater_model, local_files_only=True)
    print(f"加载 Adapter: {args.adapater_model}")
    peft_model = PeftModel.from_pretrained(model=model, model_id=str(args.adapater_model))
    if args.device:
        peft_model = peft_model.to(args.device)
    merged = peft_model.merge_and_unload()
    merged.save_pretrained(args.merged_model)
    tokenizer.save_pretrained(args.merged_model)
    print(f"合并完成: {args.merged_model}")


if __name__ == "__main__":
    main()
