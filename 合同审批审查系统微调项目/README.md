# 合同审批微调项目（contract_review_finetune）

严格沿用老师《智能汽车客服助手》的 SFT → 评估 → DPO 框架，把任务换成：

> 规则引擎已给出 `rule_hits`，模型负责写**结构化审查意见**、**语义规则判断**和**可追溯证据**。

模型只输出 JSON，最终由线上 `build_comment()` / `render_report()` 渲染成飞书 Markdown。

---

## 1. 目录结构

```text
contract_review_finetune/
├── config.py                     # ★ 唯一需要修改的参数文件
├── .env.example                  # 复制成 .env 后填 DEEPSEEK_API_KEY
├── requirements.txt
├── new_chat_template.jinja       # 老师原模板（assistant-only loss）
├── prompts/
│   ├── review_generation.md      # 摘要/关注点措辞
│   ├── semantic_generation.md    # 语义判断理由
│   ├── variant_generation.md     # 合同可控变体生成
│   └── dpo_judge.md              # DPO 同分细排（可选）
├── core/
│   ├── contract_parse.py         # 与线上 parser.py 同口径（vendored，规则变了要同步）
│   ├── rule_engine.py            # 与线上 rules.py 同口径（不含语义规则）
│   ├── snapshot.py               # 真实合同 -> input_snapshot + 种子
│   ├── report.py                 # gold 报告、校验、事实检查
│   ├── eval_utils.py             # 生成、JSON 解析、规则指标
│   ├── prompts.py
│   ├── deepseek.py
│   └── io_utils.py
├── scripts/
│   ├── 00_build_seeds.py
│   ├── 00b_generate_contract_variants.py
│   ├── 01_generate_train_validation_data.py
│   ├── 02_generate_final_test_data.py
│   ├── 03_train_sft.py
│   ├── 04_lora_merge.py
│   ├── 05_evaluate_review_schema.py
│   ├── 06_evaluate_review_judge.py
│   ├── 07_generate_dpo_data.py
│   └── 08_train_dpo_lora.py
├── data/
│   ├── raw_contracts/            # 8 份 sample + 5 份真实 OCR 合同
│   ├── variant_contracts/        # 00b 生成的合同变体
│   └── v2/
│       ├── schema/rules.json     # 30 条规则（24 条线上 + 6 条项目本地语义规则）
│       ├── schema/seed.schema.json
│       ├── 01_seeds/             # 00 生成，已随项目提供
│       ├── 02_manifests/
│       ├── 03_generated_sft_data/
│       ├── 04_final_test/
│       └── 05_dpo_data/
├── finetuned/                    # 训练产物
├── logs/                         # TensorBoard
└── eval_results/
```

---

## 2. 需要你修改的参数：只有 `config.py`

打开 [config.py](config.py)，所有 `★` 标记的段落就是需要改的。云上最少改这 6 项：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `MODEL_PATH` | `model/Qwen3-8B` | 本地基座目录 |
| `DEEPSEEK_API_KEY_ENV` | `DEEPSEEK_API_KEY` | Key 写在 `.env`，不要写进代码 |
| `OUTPUT_ROOT` | `finetuned` | SFT / merged / DPO 产物根目录 |
| `SFT_MAX_LENGTH` | `6144` | 真实合同输入+输出 BERT 上界约 6356 |
| `DPO_MAX_PROMPT_LENGTH` | `4096` | DPO prompt 上限 |
| `DPO_MAX_COMPLETION_LENGTH` | `2048` | DPO completion 上限 |

其余常用开关也都在同一文件：

- `USE_LLM_NARRATIVE`：是否让 DeepSeek 润色摘要/关注点；`False` 时全部用确定性模板。
- `VARIANTS_PER_SEED`：review 种子几个变体。
- `SEMANTIC_VARIANTS_PER_SEED`：语义种子单独多生成几个变体（默认 3）。
- `REJECT_VARIANTS_PER_SEED`：拒答种子变体数（默认 1，输出是确定性的）。
- `REJECT_SEEDS_PER_SPLIT`：每类拒答在每个 split 生成几条（默认 2，四类共 8 条）。
- `MAX_SNAPSHOT_CLAUSES` / `MAX_CLAUSE_CHARS` / `MAX_RULE_HITS`：输入裁剪。
- `SFT_*` / `DPO_*`：训练超参。
- `EVAL_*`：评估模型、生成长度、设备与 dtype。

脚本的命令行参数只用于临时覆盖，默认值全部来自 `config.py`。

---

## 3. 云上运行顺序

```bash
cd contract_review_finetune
pip install -r requirements.txt
cp .env.example .env          # 填入 DEEPSEEK_API_KEY

# 1. 下载基座（按云上实际仓库名调整）
huggingface-cli download Qwen/Qwen3-8B --local-dir model/Qwen3-8B

# 2.（可选）扩充合同变体：先 dry-run 看计划，再实际生成
python scripts/00b_generate_contract_variants.py --dry-run
python scripts/00b_generate_contract_variants.py --resume

# 3. 用父合同 + 变体重建种子（变体会强制跟随父合同同一个 split）
python scripts/00_build_seeds.py

# 4. 生成 SFT 数据（train / validation 分开跑）
python scripts/01_generate_train_validation_data.py --split train --resume
python scripts/01_generate_train_validation_data.py --split validation --resume

# 5. SFT 数据检查（只加载 tokenizer，不加载 8B 权重）与训练
python scripts/03_train_sft.py --dry-run
python scripts/03_train_sft.py --use-lora

# 可选：先用模板冒烟数据检查训练链路（不调用 DeepSeek）
python scripts/03_train_sft.py --dry-run \
  --data data/v2/03_generated_sft_data/smoke/sft_train.dryrun.jsonl \
  --eval-data data/v2/03_generated_sft_data/smoke/sft_validation.dryrun.jsonl

# 6. 合并 SFT LoRA
python scripts/04_lora_merge.py

# 7. 生成 final_test 并做规则化评估
python scripts/02_generate_final_test_data.py
python scripts/05_evaluate_review_schema.py --model finetuned/qwen3_8b_lora_merged
python scripts/06_evaluate_review_judge.py --model-a finetuned/qwen3_8b_lora_merged

# 8. 构造 DPO 数据并训练
python scripts/07_generate_dpo_data.py --resume
python scripts/08_train_dpo_lora.py --dry-run
python scripts/08_train_dpo_lora.py

# 9. 合并 DPO Adapter，做 SFT vs DPO 对比
python scripts/04_lora_merge.py \
  --base_model finetuned/qwen3_8b_lora_merged \
  --adapater_model finetuned/qwen3_8b_dpo_lora \
  --merged_model finetuned/qwen3_8b_dpo_merged

python scripts/05_evaluate_review_schema.py \
  --model finetuned/qwen3_8b_dpo_merged --name dpo
python scripts/06_evaluate_review_judge.py \
  --model-a finetuned/qwen3_8b_lora_merged \
  --model-b finetuned/qwen3_8b_dpo_merged
```

> `--resume` 只补缺失样本，不会覆盖已有数据。
> `01` 没有 `--resume` 且输出已存在时会直接报错，避免误追加。
> **重跑 `00` 后请删除旧的 `sft_train.jsonl` / `sft_validation.jsonl` 再生成**：
> 种子 ID 已按合同内容哈希固定，`01 --resume` 会检测 split 漂移并报错，但规则内容变化不会被自动识别。

---

## 4. 数据设计

### 4.1 种子分片

- `train` / `validation` / `final_test` **按合同/模板族切分**：共享任意一条 ≥20 字条款的合同会聚成同一族，整族只进入一个 split。`00` 还会做跨 split 条款 hash 检查，发现重叠直接报错（`FAIL_ON_CROSS_SPLIT_OVERLAP`）。
- 当前 13 份父合同切分为 9 / 2 / 2，不跑合同变体时生成 **63 / 18 / 17** 条种子
  （R-SEM-04~09 + 四类拒答 × 2）。
- 开 LLM 文案后，`01` 的计划约为 **145 / 31 / 17** 条 SFT 样本：
  review 2 变体、semantic 3 变体、reject 1 变体。
- 默认 `VARIANTS_PER_CONTRACT=2`，约 18 个变体，train 预计扩到 160~220；改成 3 可覆盖全部 15 个维度（含税率），train 会更多。
- 因为 `支护/模拟/示例/模架` 是同一套模板的多个版本，它们必须一起留在 train；所以 **final_test 目前只有真实合同4/5，只有法务/业务类样本，缺少付款/保证金高风险样本**。这是“无泄漏”的代价，不是 bug。
- 种子偏少，建议继续往 `data/raw_contracts/` 放 `.md`/`.txt` 合同，然后重跑 `00`。目标规模：
  - train 160~240 条；
  - validation 20~30 条；
  - final_test 40~60 条（且要包含付款/保证金/语义高风险样本）。
- 不跑变体时 DPO 只有约 40 个唯一 prompt；跑默认 18 个变体后约 150~220 个，接近设计文档建议的 200~400 对。仍建议继续补真实合同。
- `data/v2/03_generated_sft_data/smoke/` 是 `01 --dry-run` 生成的模板文本冒烟数据，**不要拿去正式训练**。

### 4.2 合同变体扩充（00b）

当前只有 13 份父合同，直接生成种子约 56 条。要扩到 160~240 条，用 `00b_generate_contract_variants.py` 做**可控变体**：

- 每个变体只改一个维度，其余条款逐字保留；
- DeepSeek 只返回 `contract_text`，**`rule_hits` / 语义判断全部由规则引擎重算**；
- 变体文件名固定 `{父合同stem}__vNN.md`，`00` 会强制变体跟随父合同同一个 split；
- 默认 `VARIANT_TRAIN_ONLY=True`，只对 train 父合同生成变体，保持 validation/final_test 是纯真实合同。

维度与业务角度：

| 维度 | 业务角度 | 影响的规则 |
|---|---|---|
| `payment_days` | 付尾款期限 | R-PAY-01 |
| `payment_ratio` | 过程付款比例 | R-PAY-02 |
| `prepay_ratio` | 预付款比例 | R-PRE-01 |
| `performance_bond` | 履约保证金 | R-DEP-01 |
| `bid_bond` | 投标保证金 | R-DEP-01 / R-SEM-09 |
| `warranty_ratio` | 质保金 | R-WAR-01 |
| `tax_rate` | 税率 | R-TAX-01 |
| `contract_amount` | 合同金额 | 金额/事实一致性 |
| `qualification` | 用户资质与合同类型对应性 | R-SEM-04 |
| `lease_method` | 租赁方式/计价方式明确性 | R-SEM-05 |
| `unfair_clause` | 霸王条款 | R-SEM-06 |
| `risk_clause` | 风险条款分配 | R-SEM-08 |
| `early_exit` | 提前退场赔偿 | R-SEM-07 |
| `party_names` | 当事人称呼 | R-SEM-02 |
| `synonym_rewrite` | 非数值条款同义改写 | rule_hits 不变 |

`R-SEM-04 ~ R-SEM-09` 是**项目本地新增的语义规则**，已写入 `data/v2/schema/rules.json`；
线上 `contract-review-service` 还没有这 6 条规则，部署时需要把它们作为 `semantic_candidates` 传进来，或同步到线上 `seed.py`。

运行：

```bash
python scripts/00b_generate_contract_variants.py --dry-run
python scripts/00b_generate_contract_variants.py --resume
python scripts/00_build_seeds.py
```

每个变体都会做五类校验：可解析、未改条款保留率、全文相似度、目标字段/语义标签确实变化、`rule_hits` 由规则引擎重算且符合预期。校验失败会重试，仍失败则跳过并写入 `data/v2/00b_variant_manifest.jsonl`。

> 改变 `VARIANTS_PER_CONTRACT` 或维度后，先清理 `data/variant_contracts/` 再重跑；
> `00` 会读取目录里所有变体文件，不会自动删除旧变体。
>
> `synonym_rewrite` 会指定一条具体条款并要求至少改动 30% 字词；如果模型原样返回，
> 校验会拒绝并重试。`--resume` 会跳过 manifest 中 `status=skipped` 的条目；
> 修完 prompt 后只想重试这些失败项，请用 `--resume --retry-skipped`（不会动已成功的变体）。
> `VARIANT_SYNONYM_TEMPERATURE` 单独控制同义改写的采样温度（默认 0.7）。

### 4.3 三类样本

| `sample_type` | 输入 | 输出 |
|---|---|---|
| `review` | 完整 `input_snapshot` | 完整报告 JSON（风险等级 + 命中规则 + 摘要 + 关注点 + **关注点出处** + 语义判断） |
| `semantic` | 局部快照 + 1~2 条语义候选 | `{"semantic_judgements":[...]}` |
| `reject` | 解析失败 / 关键字段缺失 / 超范围 / 语义无法判断 | `{"status":"rejected","reject_type":...,"reason":...,"needed":[...]}` |

`reject_type` 有四种，对应 refusal 维度的四类边界：

- `parse_failed`：OCR/解析失败；
- `insufficient`：关键字段/条款缺失，规则引擎无法完成阈值判断；
- `out_of_scope`：不属于工程/租赁/分包审查范围；
- `semantic_unknown`：缺少成本分析表等支撑材料，语义规则无法判断。

`review` 的 `focus_points` 保持字符串列表，新增一一对应的 `focus_point_refs`：

```json
{
  "focus_points": ["付款周期超标（R-PAY-01）：..."],
  "focus_point_refs": [
    {"kind": "rule", "rule_code": "R-PAY-01", "evidence_position": "第三条（第1页）"}
  ]
}
```

`kind` 允许 `rule` / `semantic` / `general`；`general` 是通用复核项，没有规则来源。

### 4.4 结构化结论不由模型自由发挥

- `overall_risk_level`、`hit_rules`、`semantic_judgements.hit` 由规则引擎和种子 gold 锁定；
- DeepSeek 只写 `summary`、`focus_points`、`semantic_reasons`；
- `focus_point_refs` 由 gold 生成，LLM 不负责填写；LLM 改写的 `focus_points` 数量
  必须与 gold refs 一致，否则回退模板，避免“关注点 -> 出处”错位；
- 写完后经过 `core/report.py` 校验：规则集合、证据子串、证据位置、风险等级、
  数字/规则号编造、关注点出处（provenance）。
- `_fit_summary()` 会把兜底摘要稳定压到 80~400 字，避免模板自身越界导致 `01` 中断。
- 校验失败会重试，仍失败则回退确定性模板，不会把脏数据写进训练集。

### 4.5 不使用 `tools`

规则、条款、命中结果全部在 `messages[0].content` 的 JSON 里。
样本没有 `tools` 字段，避免 Qwen 模板走 function-calling 分支。

---

## 5. 训练与长度

| 阶段 | 参数 | 默认 |
|---|---|---|
| SFT | `max_length` | 6144 |
| SFT | LoRA r / alpha / dropout | 16 / 16 / 0.05 |
| SFT | epochs / batch / eval-batch / grad-accum | 3 / 2 / 1 / 8 |
| SFT | learning rate | 2e-4（LoRA） |
| DPO | beta / lr / epochs | 0.1 / 5e-6 / 1 |
| DPO | max prompt / completion | 4096 / 2048 |

依赖版本已在本地验证：`transformers==5.2.0` + `trl==0.24.0` + `peft>=0.20`。
TRL 0.24 会写 `PreTrainedModel.warnings_issued`，而 transformers 5.x 已移除该属性；
`08_train_dpo_lora.py` 里加了兼容补丁。**不要随意升级 transformers 5.x 小版本**，
否则 `logging_dir` 等 API 可能再次失效。

24GB 显存如果 OOM，按顺序尝试：

1. `SFT_BATCH_SIZE` 2 → 1；
2. `SFT_MAX_LENGTH` 6144 → 5120；
3. `MAX_SNAPSHOT_CLAUSES` 4 → 3、`MAX_CLAUSE_CHARS` 1200 → 900；
4. 确认已开 `gradient_checkpointing`（脚本默认开）。

`03_train_sft.py --dry-run` 会打印 token 长度分布；超过 `SFT_MAX_LENGTH` 会直接报错。

---

## 6. GPU 选型

Qwen3-8B 用 LoRA 微调，bf16 权重约 16GB；全参微调不在本项目范围内。

| 用途 | 24GB（4090/A10） | 48GB（L40S/A6000） | 80GB（A100/H100） |
|---|---|---|---|
| SFT（6144 长度） | 需 `batch=1`，可能再降到 5120 | 默认可用 | 默认可用，可加 batch |
| DPO（4096+2048） | 紧张，建议降到 3072+1536 | 默认可用 | 默认可用 |
| 05/07 单模型生成 | 可用 | 可用 | 可用，速度快 |
| 06 双模型 pairwise | 不建议 | 勉强，建议先 pointwise | 推荐 |
| 04 LoRA 合并 | CPU 需 32GB 内存 | 可用 | 可用 |

推荐：

- **最低可跑**：24GB。把 `SFT_BATCH_SIZE=1`、`SFT_EVAL_BATCH_SIZE=1`、
  `SFT_MAX_LENGTH=5120`、`DPO_MAX_PROMPT_LENGTH=3072`、
  `DPO_MAX_COMPLETION_LENGTH=1536`，并把 `MAX_SNAPSHOT_CLAUSES` 降到 3。
- **推荐**：48GB。保持默认长度，SFT/DPO/单模型评估都比较宽松。
- **最省心**：80GB。可以保留默认值并把 `SFT_BATCH_SIZE` 提到 4，06 pairwise 也能直接跑。
- **不需要多卡**：8B + LoRA 单卡足够；多卡只会增加部署复杂度。
- **CPU 也能做的步骤**：`00`、`01`（只调 DeepSeek）、`02`、`04`（合并需要 32GB 内存）。
  `03/05/06/07/08` 需要 GPU。

其他配置：内存 ≥32GB，磁盘 ≥100GB（基座 16GB + 两个 merged 模型 + checkpoint）。

## 7. 评估指标

`05_evaluate_review_schema.py` 输出：

- `json_valid`：报告是否可解析；
- `schema_valid`：是否通过完整校验；
- `rule_precision / recall / f1`：命中规则集合；
- `evidence_rate`：证据能否在条款原文或输入命中中溯源；
- `position_accuracy`：证据位置是否合法；
- `risk_accuracy`：整体风险等级；
- `semantic_accuracy`：语义判断一致率；
- `focus_provenance_rate`：关注点能回溯到 rule_hit / semantic_candidate 的比例；
- `fabrication_count`：输入之外的规则号/数字/条款号个数；
- `reject_accuracy`：拒绝边界是否通过完整校验；
- `reject_type_accuracy`：四类 `reject_type` 是否判断正确。

结果写入 `eval_results/schema_<模型名>.json` 和逐条 `_details.jsonl`。

`06_evaluate_review_judge.py`：

- 只给 `--model-a`：pointwise 评分（摘要 1~5、关注点 1~5）；
- 同时给 `--model-a` / `--model-b`：pairwise Win Rate（B 相对 A）。

`06` 现在采用**顺序加载**：先加载 A 生成全部答案并释放显存，再加载 B 生成，
最后只保留 Judge 客户端做比较。这样不需要两个 8B 同时驻留，
24GB/48GB 卡也不容易触发 CPU offload；代价是模型加载时间变长。

---

## 7. 与老师脚本的对应关系

| 老师 | 本项目 | 主要改动 |
|---|---|---|
| 01 生成 SFT | `01_generate_train_validation_data.py` | 输入快照替代用户对话；gold 结构化；无 tools |
| 02 final_test | `02_generate_final_test_data.py` | 输出 input_snapshot + expected_report；不调用 LLM |
| 03 SFT | `03_train_sft.py` | sample_type 改为 review/semantic/reject；max_length 6144 |
| 04 merge | `04_lora_merge.py` | 参数来自 config；支持 DPO Adapter 二次合并 |
| 05 eval | `05_evaluate_review_schema.py` | 新写：规则/证据/风险/编造/拒绝 |
| 06 eval | `06_evaluate_review_judge.py` | 新写：pointwise + pairwise |
| 07 DPO 数据 | `07_generate_dpo_data.py` | 只用 train；完整快照作为 prompt；硬指标筛选 |
| 08 DPO | `08_train_dpo_lora.py` | prompt 4096 / completion 2048 |

---

## 8. 注意事项

1. **final_test 只用于最终评估**，不要拿去生成 DPO 数据，也不要调参。
2. **不要手改 `rules.json`**；规则变了先改线上 `seed.py`，再重新导出。
3. **语义规则 R-SEM-01（成本分析一致性）当前不生成样本**：输入里没有成本分析表，无法可靠标注 gold。等线上补了成本分析数据再加。
4. **starter 种子是自动生成的**：建议人工抽检 30~50 条，重点看证据是否在原文、语义 label 是否合理。
5. **`.env` 不要提交**；README 和代码都不记录真实 Key。
6. 本项目只读 `.md/.txt` 合同；PDF 请先在线上系统用 MinerU 转成 Markdown。
