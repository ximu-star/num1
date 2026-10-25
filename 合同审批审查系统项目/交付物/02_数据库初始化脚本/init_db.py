"""合同审批审查系统数据库初始化脚本。

默认：建表 + 灌入 24 条初始规则（不删除已有数据）。
--reset：先删表再重建，然后灌规则（会清空数据，谨慎使用）。

运行前请先执行 apply_config.py，确保 contract-review-service/.env 存在。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "01_可运行工具服务" / "contract-review-service"

if not SERVICE_DIR.exists():
    raise SystemExit(f"找不到服务目录：{SERVICE_DIR}")

os.chdir(SERVICE_DIR)
sys.path.insert(0, str(SERVICE_DIR))

from contract_review_service.database import SessionLocal, init_db  # noqa: E402
from contract_review_service.seed import seed_demo_data  # noqa: E402


def main() -> None:
    if "--reset" in sys.argv:
        from contract_review_service.reset_data import reset_data

        reset_data()
        print("数据库已重置（删表重建 + 24 条规则）")
        return

    init_db()
    with SessionLocal() as db:
        seed_demo_data(db)
    print("数据库初始化完成（建表 + 24 条规则）")


if __name__ == "__main__":
    main()
