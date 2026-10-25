# 合同审批审查系统 · 交付物

## 目录

| 目录/文件 | 内容 |
|---|---|
| `01_可运行工具服务/` | 8001 业务服务、8002 AI 服务、5174 前端、启动脚本、配置模板 |
| `02_数据库初始化脚本/` | `init_db.py`、`schema.sql`、`seed_rules.sql` |
| `03_示例合同/` | 通用服务合同（非标/标准）、Word 示例、`approvals.json` |
| `04_工具调用说明/` | 内部接口、管理接口、Skill/Tool 清单、端到端调用示例 |
| `01_可运行工具服务/运行说明.md` | 安装、配置、启动、演示步骤 |

## 快速开始

```powershell
cd 01_可运行工具服务
copy config.env.example config.env
python apply_config.py

cd ai-service
python -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pip install -e ..\contract-review-service
cd ..

ai-service\.venv\Scripts\python.exe ..\02_数据库初始化脚本\init_db.py
.\start-all.ps1
```

默认配置为 **SQLite + 本地存储 + MinerU mock + Milvus 关闭**，不需要 PostgreSQL/MinIO/MinerU 即可跑通。

## 示例结果

- `HT-2026-007` 通用服务合同（非标）：高风险，15 条命中
- `HT-2026-008` 通用服务合同（标准）：低风险，0 条命中

## 说明

- 交付物**不包含任何真实密钥**；`config.env.example` 需要填写后再用。
- 8002 必须用 `python -m contract_review.main` 启动。
- 真实飞书客户端、Milvus 语义检索、MinerU 云端/本地部署需要按 `运行说明.md` 和 `工具调用说明.md` 配置。
- 完整接口和 Tool 参数见 `04_工具调用说明/工具调用说明.md`。
