# 合同审批审查系统（2.4）

基于电商小二 `customer-service` + `ai-service` 框架改造，对应《合同审批助手设计文档 v1.9》。

## 一、目录结构

```
合同审批助手/
├── config.env                     # 统一配置：URL / Token / API Key（已 gitignore）
├── apply_config.py                # 把 config.env 分发到两个服务
├── contract-review-service/       # 8001 业务主服务（原 ecommerce-service）
│   ├── contract_review_service/
│   │   ├── main.py                # FastAPI 路由（内部接口 + 管理后台接口）
│   │   ├── models.py              # 8 张表
│   │   ├── seed.py                # 24 条初始规则
│   │   ├── clients/               # 飞书 mock、存储
│   │   └── services/              # 状态机、解析、规则、审查、回写、调度
│   └── samples/                   # 2 份样例合同 + approvals.json
├── ai-service/                    # 8002 AI 服务（原 ai-service）
│   └── contract_review/
│       ├── agent/harness/skills/  # 4 个合同 Skill
│       ├── agent/harness/tools/   # 8 个合同 Tool + load_skill
│       ├── agent/harness/rules/   # 合同事实校验 + 写操作校验
│       └── agent/llm/             # 合同审查结构化输出与 Prompt
└── frontend/                      # 5174 Vue 管理后台
```

## 二、架构

```
飞书审批（mock / real）
    │ 拉待办、回写评论
    ▼
8001 ContractReviewService（唯一写业务库）
    ├─ 定时拉待办 → 附件哈希判更新 → 解析 → PG 规则 → 落库 → 回写
    ├─ 8 张表 / 状态机 / failed_stage 重试 / task_logs
    └─ 内部接口 ← 8002 Tool 调用
    ▼
8002 AIService
    ├─ 4 Skill：APPROVAL_FETCH / CONTRACT_PARSE / RULE_REVIEW / REPORT_WRITEBACK
    ├─ 8 Tool：拉待办/详情/附件/解析/规则/语义/结果/回写
    └─ Harness：结构校验 + 事实校验 + 写操作校验
    ▼
5174 Vue 管理后台：任务监控 / 失败重试 / 规则配置 / 日志
```

## 三、快速启动

### 0. 统一配置（推荐）

所有 URL、Token、API Key 都集中在根目录 [config.env](config.env)：

```bash
# 1. 编辑 config.env，至少填写：
#    LLM_API_KEY        （跑 8002 Agent 需要）
#    CONTRACT_MINERU_TOKEN + CONTRACT_MINERU_MODE=api（真实 MinerU 需要）
#    CONTRACT_FEISHU_APP_ID / APP_SECRET + CONTRACT_FEISHU_MODE=real（真实飞书需要）

# 2. 分发到两个服务
python apply_config.py

# 3. 重启 8001 / 8002
```

`apply_config.py` 会生成 `contract-review-service/.env` 和 `ai-service/.env`，并把原文件备份为 `.env.bak`。`config.env` 已加入 `.gitignore`，不会提交密钥。可用 `python apply_config.py --dry-run` 先看会写什么。

### 1. 8001 业务服务

项目统一使用 `ai-service/.venv` 里的 Python（已安装 8001 和 8002 的依赖）：

```bash
cd contract-review-service
../ai-service/.venv/Scripts/python.exe -m contract_review_service.reset_data
../ai-service/.venv/Scripts/python.exe -m uvicorn contract_review_service.main:app --host 127.0.0.1 --port 8001
```

默认使用 SQLite（`contract_review.db`），无需额外数据库。切 PostgreSQL：

```bash
# .env
CONTRACT_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/contract_review
```

### 2. 8002 AI 服务

```bash
cd ai-service
# 必须用 main 启动：Selector 事件循环才兼容 psycopg 异步连接
./.venv/Scripts/python.exe -m contract_review.main
```

`.env` 中 `LLM_API_KEY` 需换成真实 key 才能跑通 Agent；只跑 8001 的确定性流水线不需要 LLM。

### 3. 5174 管理后台

```bash
cd frontend
npm install
npm run dev
```

访问 http://127.0.0.1:5174。

## 四、演示流程

1. 打开后台，点击「拉取待办」。
2. 系统从 `samples/approvals.json` 拉取 4 份合同：
   - `HT-2026-001` 支护工程合同（非标，Markdown）：命中 13 条规则，整体高风险。
   - `HT-2026-002` 模架工程合同（标准，Markdown）：0 命中，整体低风险。
   - `HT-2026-003` 支护工程合同（扫描件 PDF）：走 MinerU OCR，`is_scanned=true`，评论开头提示人工核对金额/日期。
   - `HT-2026-004` 支护工程合同（Word）：先转 PDF，再走 MinerU，命中 12 条规则，整体高风险。
- `HT-2026-005` 模架工程合同（MinIO 测试）：附件写入 MinIO，0 命中，整体低风险。
3. 点击任务行查看详情：附件、条款、命中证据、风险摘要、运行日志。
4. 在任务详情点「触发审查」可重新执行；blocked 任务点「从失败步骤重试」。
5. 回写结果写到 `contract-review-service/storage/mock_comments/{approval_code}.md`。
6. 「规则配置」可启停规则；「运行日志」查看全链路 Skill/Tool 调用。

命令行验证：

```bash
curl -X POST http://127.0.0.1:8001/api/v1/admin/pull
curl http://127.0.0.1:8001/api/v1/admin/tasks
curl http://127.0.0.1:8001/api/v1/admin/tasks/1
```

## 五、规则来源（《商务岗操作手册》）

| 手册位置 | 规则示例 |
|---|---|
| 5.3.3 合同审核要点 | 付款周期、付款比例、履约保证金≤10%、质保金、税率、承包范围、工期、计量结算、质量标准 |
| 5.4.2 客户付款条件底线 | 过程付款比例不低于结算额 50% |
| 5.3.2 非标合同预审 | 非标合同分流；合同与确认函不一致 |
| 5.3.4 / 5.4.1 印章与客户角色 | 合同专用章、项目部章、管辖、保密、知识产权 |
| 8.4 垫资风险 | 逾期 90 天预警、120 天停建（可扩展为应收预警工具） |

规则表字段：`target_field + operator + threshold_unit` 保证阈值规则可执行；`is_semantic` 标记 Milvus 语义规则。

## 六、主要接口

### 内部接口（供 8002 Tool 调用，`X-Internal-Token`）

- `POST /internal/v1/approvals/pull`
- `POST /internal/v1/reviews/trigger`
- `GET /internal/v1/tasks`
- `GET /internal/v1/tasks/{approval_code}`
- `POST /internal/v1/tasks/{approval_code}/retry`
- `POST /internal/v1/tasks/{approval_code}/comment`

### 管理后台接口

- `GET /api/v1/admin/summary`
- `GET /api/v1/admin/tasks`
- `GET /api/v1/admin/tasks/{task_id}`
- `POST /api/v1/admin/tasks/{task_id}/retry`
- `POST /api/v1/admin/tasks/{task_id}/review?force=true`
- `PATCH /api/v1/admin/tasks/{task_id}/standard-version?is_standard=true`
- `GET /api/v1/admin/rules`、`POST`、`PATCH /{id}`
- `GET /api/v1/admin/logs`

## 七、配置与边界

| 项 | 默认 | 说明 |
|---|---|---|
| 飞书 | `CONTRACT_FEISHU_MODE=mock` | mock 读 `samples/approvals.json`；real 待 Phase 0 验证后实现 |
| MinerU | `CONTRACT_MINERU_MODE=mock` | `mock` 读同名 `.md`；`api` 调 MinerU 云端（需 token）；`cli` 调本地 `mineru` 命令 |
| 扫描件 OCR | 自动检测 | PDF 文本层不足 50% 判为扫描件，MinerU 走 OCR（api 用 vlm，cli 用 `-m ocr`） |
| Milvus | `CONTRACT_MILVUS_ENABLED=false` | 未启用时语义规则自动跳过，只跑 PG 规则并记录 warn |
| 存储 | `local` | 附件写 `storage/`；MinIO 适配器预留 |
| 定时任务 | 每 60 秒 | asyncio 调度，替代 APScheduler，少一个依赖 |
| 模型 | DeepSeek/OpenAI 兼容 | 8002 Agent 需要真实 `LLM_API_KEY` |

**明确未做 / 后续**：真实飞书事件订阅与评论 API、Milvus 建库与 BGE-M3 向量化、标准版合同自动识别、模型微调。

## 八、MinerU 与扫描件 OCR

PDF 和 jpg/png 图片统一走 `clients/mineru.py`：

| 模式 | 配置 | 行为 |
|---|---|---|
| `mock` | `CONTRACT_MINERU_MODE=mock` | 读与 PDF 同名的 `.md`（优先同目录，其次 `samples/`），用于本地闭环演示 |
| `api` | `CONTRACT_MINERU_MODE=api` + `CONTRACT_MINERU_TOKEN` | 调 MinerU 云端 v4：申请上传地址 → 上传 → 轮询 → 下载 zip → 解压 `full.md` |
| `cli` | `CONTRACT_MINERU_MODE=cli` + `CONTRACT_MINERU_CLI_COMMAND` | 调本地 MinerU：`mineru -p input.pdf -o out -m auto/ocr` |

- **扫描件判定**：`detect_scanned()` 用 pymupdf 统计有文本层的页数，低于 50% 判为扫描件；图片一律按扫描件处理。
- **OCR**：扫描件在 API 模式使用 `model_version=vlm`，CLI 模式使用 `-m ocr`；非扫描件用 `auto`。
- **页码定位**：MinerU `*_content_list.json` 的 `page_idx` 会被注入为 `[[PAGE=n]]`，解析器据此给出条款页码。
- 扫描件任务的评论开头会自动加：“本合同为扫描件识别，关键金额/日期建议人工核对。”

### MinIO 时钟偏差

- S3 签名要求客户端与 MinIO 服务器时间差不超过 15 分钟；超过会报 `RequestTimeTooSkewed`。
- 客户端会自动读取 MinIO 的 `Date` 头并校正签名时间（`CONTRACT_MINIO_AUTO_CLOCK_SYNC=true`）。
- 附件上传后会在 `storage/working/` 保留一份本地工作副本，供 MinerU / Word 转换读取。
- 生产环境仍建议校准 MinIO 服务器和业务服务器的 NTP。

### Milvus 语义检索

- 首次使用先导入手册切片：
  ```bash
  cd contract-review-service
  ../ai-service/.venv/Scripts/python.exe scripts/import_manual.py --reset
  ```
- BGE-M3 权重约 2.2GB，加载需要约 1.2–1.5GB 可用内存；内存不足时设 `CONTRACT_MILVUS_ENABLED=false`，系统会自动只跑 PG 确定性规则。
- 为降低内存占用，`CONTRACT_BGE_DTYPE=bfloat16`，并启用 `low_cpu_mem_usage`。
- Windows 上 `services/semantic.py` 已关闭 tokenizer 并行（`TOKENIZERS_PARALLELISM=false`），否则 encode 可能死锁。

### Word 转 PDF

`.doc/.docx` 附件不再用 python-docx 直接抽文本，而是先转 PDF，再统一交给 MinerU：

1. **LibreOffice**（服务器推荐）：`soffice --headless --convert-to pdf`；可用 `CONTRACT_SOFFICE_PATH` 指定路径。
2. **Word COM**（Windows 本地兜底）：安装 Microsoft Word + `pywin32`，自动调用 Word 转 PDF。
3. 转换后的 PDF 存到 `storage/converted/{task_id}/v{版本}/`，并记录在 `approval_attachments.converted_file_path`。
4. 两种转换器都不可用时，任务进入 `blocked`，错误信息提示安装 LibreOffice 或 Word。

验证样例：`HT-2026-004` 是 Word 合同，转换后走 MinerU，命中 12 条规则，整体高风险。

## 九、与设计文档的对应

- 8 张表：`approval_tasks / approval_attachments / contract_parses / review_rules / rule_hits / review_results / comment_logs / task_logs`
- 状态机：`pending → parsing → ready → reviewing → done`，失败进 `blocked` 并记录 `failed_stage`
- 附件更新：`file_token` / `file_hash` 变化 → `attachment_version+1`，旧解析标记 `superseded`
- 回写去重：`comment_logs` 同一 `task + attachment_version` 成功一次后跳过
- 事实校验：风险点必须引用工具返回的 `rule_hits`（含 `rule_code`）
- 写操作校验：评论非空、长度上限、重复回写由 8001 拦截
