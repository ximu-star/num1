# 数据库初始化脚本

## 文件说明

| 文件 | 用途 |
|---|---|
| `init_db.py` | 推荐方式：按当前 SQLAlchemy 模型建表，并灌入 24 条初始规则 |
| `schema.sql` | PostgreSQL DDL（8 张表、索引、唯一约束），供 DBA 手工执行 |
| `seed_rules.sql` | 24 条初始规则的 INSERT，可重复执行 |

## 推荐用法

```powershell
cd 01_可运行工具服务
copy config.env.example config.env
python apply_config.py

# 建表 + 灌规则（不删除已有数据）
..\ai-service\.venv\Scripts\python.exe ..\02_数据库初始化脚本\init_db.py

# 清空重建（谨慎）
..\ai-service\.venv\Scripts\python.exe ..\02_数据库初始化脚本\init_db.py --reset
```

## PostgreSQL 手工初始化

1. 先创建数据库：

```sql
CREATE DATABASE contract_review;
```

2. 执行 DDL：

```bash
psql -h 127.0.0.1 -U postgres -d contract_review -f schema.sql
```

3. 可选：灌入规则：

```bash
psql -h 127.0.0.1 -U postgres -d contract_review -f seed_rules.sql
```

## 表清单

`approval_tasks`、`approval_attachments`、`contract_parses`、`review_rules`、`rule_hits`、`review_results`、`comment_logs`、`task_logs`。

## 注意

- `init_db.py` 读取 `01_可运行工具服务/contract-review-service/.env` 里的数据库地址。
- `--reset` 会删除所有表和数据，只建议在开发环境使用。
- `schema.sql` 使用 PostgreSQL 方言；SQLite 请用 `init_db.py`。
