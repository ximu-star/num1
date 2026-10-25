"""把根目录 config.env 分发到两个服务的 .env。

用法：
    python apply_config.py
    python apply_config.py --dry-run

规则：
- 8001 contract-review-service/.env 只写入 CONTRACT_ 开头的键；
- 8002 ai-service/.env 只写入 AI_ / API_ / INTERNAL_ / CONTRACT_REVIEW_ / JWT_ / LLM_ 开头的键；
- 写入前会备份原 .env 为 .env.bak。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.env"
REVIEW_ENV = ROOT / "contract-review-service" / ".env"
AI_ENV = ROOT / "ai-service" / ".env"

REVIEW_PREFIXES = ("CONTRACT_",)
AI_PREFIXES = (
    "AI_",
    "API_",
    "INTERNAL_",
    "CONTRACT_REVIEW_",
    "JWT_",
    "LLM_",
)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def filter_env(values: dict[str, str], prefixes: tuple[str, ...]) -> dict[str, str]:
    return {
        key: value
        for key, value in values.items()
        if key.startswith(prefixes)
    }


def render(values: dict[str, str], title: str) -> str:
    lines = [f"# 由 apply_config.py 生成：{title}", "# 修改请编辑根目录 config.env 后重新运行", ""]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def write_env(target: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] 将写入 {target}")
        return
    if target.exists():
        shutil.copy2(target, target.with_suffix(".env.bak"))
    target.write_text(content, encoding="utf-8")
    print(f"已写入 {target}")


def mask(values: dict[str, str]) -> str:
    secret_keys = [key for key in values if "KEY" in key or "TOKEN" in key or "SECRET" in key]
    if not secret_keys:
        return "没有检测到密钥字段"
    lines = []
    for key in secret_keys:
        value = values.get(key, "")
        lines.append(f"  {key}={'***已填写***' if value else '（空）'}")
    return "\n".join(lines)


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if not CONFIG_FILE.exists():
        raise SystemExit(f"找不到 {CONFIG_FILE}")

    values = parse_env(CONFIG_FILE)
    review_values = {
        key: value
        for key, value in filter_env(values, REVIEW_PREFIXES).items()
        if not key.startswith("CONTRACT_REVIEW_")
    }
    ai_values = filter_env(values, AI_PREFIXES)

    write_env(REVIEW_ENV, render(review_values, "8001 ContractReviewService"), dry_run)
    write_env(AI_ENV, render(ai_values, "8002 ContractReview AI Service"), dry_run)

    print("\n配置检查：")
    print(mask(values))
    print("\n完成。请重启 8001 和 8002 使配置生效。")
    if values.get("CONTRACT_MINERU_MODE") == "mock" and values.get("CONTRACT_MINERU_TOKEN"):
        print("提示：已填写 MINERU_TOKEN，但 MINERU_MODE 仍是 mock；如需云端 MinerU 请改成 api。")
    if values.get("CONTRACT_FEISHU_MODE") == "real" and not values.get("CONTRACT_FEISHU_APP_ID"):
        print("提示：FEISHU_MODE=real，但 FEISHU_APP_ID 为空。")


if __name__ == "__main__":
    main()
