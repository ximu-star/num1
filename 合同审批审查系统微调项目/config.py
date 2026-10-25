"""合同审批微调项目 · 唯一参数配置文件。

===============================================================================
★ 你只需要修改本文件。
★ 所有脚本（00~08）都从这里读取路径、模型、超参和长度预算；
★ 脚本命令行参数只用于临时覆盖，不作为项目默认值。
===============================================================================

云上运行最少要改的 6 项：
  1. MODEL_PATH              本地 Qwen3-8B 目录（或 HuggingFace 缓存目录）
  2. DEEPSEEK_API_KEY_ENV    环境变量名（默认 DEEPSEEK_API_KEY，值写在 .env）
  3. OUTPUT_ROOT             训练产物根目录（finetuned/ 和 logs/ 的父目录）
  4. SFT_MAX_LENGTH          SFT 最大序列长度（本项目的实测建议 6144）
  5. DPO_MAX_PROMPT_LENGTH   DPO prompt 上限（建议 4096）
  6. DPO_MAX_COMPLETION_LENGTH DPO completion 上限（建议 2048）
"""

from __future__ import annotations

from pathlib import Path

# =============================================================================
# ★ 1. 项目路径（云上只需要改这里）
# =============================================================================
ROOT = Path(__file__).resolve().parent

# ★ 微调基座模型目录。云上如果从 HuggingFace 下载，改成缓存目录或本地目录。
MODEL_PATH = ROOT / "model" / "Qwen3-8B"

# ★ 训练产物根目录。SFT / merged / DPO 都写到这里。
OUTPUT_ROOT = ROOT / "finetuned"
LOG_ROOT = ROOT / "logs"

# ★ 原始合同目录（只有 00_build_seeds.py 会读；支持 .md / .txt）。
RAW_CONTRACTS_DIR = ROOT / "data" / "raw_contracts"

# ★ 合同变体目录（00b 写入、00 读取；变体必须跟随父合同同一个 split）。
VARIANT_CONTRACTS_DIR = ROOT / "data" / "variant_contracts"

# 数据版本目录与子目录（一般不用改）
DATA_ROOT = ROOT / "data" / "v2"
SCHEMA_DIR = DATA_ROOT / "schema"
SEEDS_DIR = DATA_ROOT / "01_seeds"
MANIFEST_DIR = DATA_ROOT / "02_manifests"
SFT_DATA_DIR = DATA_ROOT / "03_generated_sft_data"
FINAL_TEST_DIR = DATA_ROOT / "04_final_test"
DPO_DATA_DIR = DATA_ROOT / "05_dpo_data"
VARIANT_MANIFEST_PATH = DATA_ROOT / "00b_variant_manifest.jsonl"

RULES_PATH = SCHEMA_DIR / "rules.json"
SEED_SCHEMA_PATH = SCHEMA_DIR / "seed.schema.json"
CHAT_TEMPLATE_PATH = ROOT / "new_chat_template.jinja"
ENV_FILE = ROOT / ".env"


# =============================================================================
# ★ 2. DeepSeek（造数据 / Judge）
# =============================================================================
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_TIMEOUT_SECONDS = 240
DEEPSEEK_MAX_RETRIES = 3
DEEPSEEK_RETRY_SLEEP_SECONDS = 3

# 造数据时的采样参数
DEEPSEEK_TEMPERATURE = 0.8
DEEPSEEK_MAX_TOKENS = 4096
# 说明：01 逐条调用 DeepSeek（每条快照 + gold 已经较大），不做批量拼接。

# Judge 参数
JUDGE_MODEL = "deepseek-v4-flash"
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 512


# =============================================================================
# ★ 3. 数据构造（00 / 01 / 02）
# =============================================================================
# 每条种子生成多少个 SFT 变体。变体只改变摘要/关注点措辞，不改变规则结论。
VARIANTS_PER_SEED = 2
# 语义样本单独多生成几个变体：语义判断是当前最薄弱的能力，需要更多样本。
SEMANTIC_VARIANTS_PER_SEED = 3
# 拒答样本输出是确定性的，重复变体没有意义，保持 1。
REJECT_VARIANTS_PER_SEED = 1

# 合同级切分比例：同一份合同只进入一个 split，避免条款泄漏。
VALIDATION_CONTRACT_RATIO = 0.15
FINAL_TEST_CONTRACT_RATIO = 0.15

# 是否额外生成一条“全量命中”的 review 种子（会更大、更接近真实报告）
INCLUDE_FULL_REVIEW_SEED = True

# 是否生成 reject 边界样本；每个 split 会按 REJECT_TYPES 各生成
# REJECT_SEEDS_PER_SPLIT 条（默认 4 类 × 2 条 = 8 条）。
INCLUDE_REJECT_SEEDS = True
REJECT_SEEDS_PER_SPLIT = 2

# 00 构建完成后，若 train/validation/final_test 之间仍有完全相同条款，直接报错。
# 如果合同里确实有无法避免的通用样板条款，可设为 False，但 final_test 指标要谨慎解读。
FAIL_ON_CROSS_SPLIT_OVERLAP = True

# 快照裁剪：控制输入 token。真实合同实测约 2.6k~5k BERT token。
MAX_SNAPSHOT_CLAUSES = 4
MAX_CLAUSE_CHARS = 1200
MAX_RULE_HITS = 12
MAX_SEMANTIC_CANDIDATES = 2

# 报告文本约束（同时用于生成校验和评估）
MIN_SUMMARY_CHARS = 80
MAX_SUMMARY_CHARS = 400
MIN_FOCUS_POINTS = 3
MAX_FOCUS_POINTS = 5
MAX_FOCUS_POINT_CHARS = 120

# 生成时如果校验失败，重试次数；超过后回退到确定性模板文本。
NARRATIVE_RETRIES = 2

# 01/02 是否调用 DeepSeek 生成摘要措辞。
# 设为 False 时全部使用确定性模板，最快、零成本，适合先跑通链路。
USE_LLM_NARRATIVE = True


# =============================================================================
# ★ 3.1 合同变体扩充（00b）
# =============================================================================
# 每份父合同最多生成几个变体。2 个/合同约能把 train 从 56 扩到 160~220；
# 想覆盖全部 15 个维度（含税率）可用 3；不要盲目调到 6，种子会过多。
VARIANTS_PER_CONTRACT = 2

# 只对落在 train 的父合同生成变体，保持 validation/final_test 是纯真实合同。
VARIANT_TRAIN_ONLY = True

# 变体文件名：{父合同stem}__v01.md
VARIANT_FILE_PATTERN = r"^(?P<stem>.+?)__v(?P<seq>\d+)\.(?P<ext>md|txt)$"

# 维度轮转顺序。每个变体只改一个维度，规则结论由 run_rules 重算。
VARIANT_DIMENSION_ORDER = (
    "payment_days",        # 付尾款期限
    "payment_ratio",       # 过程付款比例
    "prepay_ratio",        # 预付款比例
    "performance_bond",    # 履约保证金
    "bid_bond",            # 投标保证金
    "warranty_ratio",      # 质保金
    "tax_rate",            # 税率
    "contract_amount",     # 合同金额
    "qualification",       # 用户资质与合同类型对应性
    "lease_method",        # 租赁方式/计价方式明确性
    "unfair_clause",       # 霸王条款
    "risk_clause",         # 风险条款
    "early_exit",          # 提前退场赔偿
    "party_names",         # 当事人称呼
    "synonym_rewrite",     # 非数值条款同义改写
)

VARIANT_TEMPERATURE = 0.3
# synonym_rewrite 需要模型真的换一种说法，温度太低会原样复制。
VARIANT_SYNONYM_TEMPERATURE = 0.7
VARIANT_MAX_TOKENS = 8192
VARIANT_RETRIES = 2
VARIANT_MIN_CLAUSES = 2
VARIANT_MIN_UNCHANGED_CLAUSE_RATIO = 0.6
# 当事人称呼替换会动到很多条款，条款保留率不设门槛，由全文相似度兜底。
VARIANT_PARTY_MIN_UNCHANGED_CLAUSE_RATIO = 0.0
VARIANT_MIN_SIMILARITY = 0.85
VARIANT_SYNONYM_MIN_SIMILARITY = 0.5
VARIANT_MAX_SOURCE_BYTES = 12_000


# =============================================================================
# ★ 4. SFT 训练（03）
# =============================================================================
USE_LORA = True
SFT_EPOCHS = 3.0
SFT_BATCH_SIZE = 2
# ★ eval batch 单独设 1：SFTConfig 默认 8，在 6144 长度下 24GB 卡会 OOM。
SFT_EVAL_BATCH_SIZE = 1
SFT_GRADIENT_ACCUMULATION_STEPS = 8
SFT_LEARNING_RATE = 2e-4          # LoRA 学习率
SFT_FULL_LEARNING_RATE = 2e-5     # 全参微调学习率（--no-lora 时使用）
# ★ 实测：2048 一定不够；真实合同输入+输出 BERT 上界约 6356。
SFT_MAX_LENGTH = 6144
SFT_LOGGING_STEPS = 1
SFT_SAVE_STEPS = 50
SFT_EVAL_STEPS = 50
SFT_SAVE_TOTAL_LIMIT = 2
SFT_SEED = 42
# 渲染后超过 SFT_MAX_LENGTH 时是否直接报错（建议 True，先发现再裁剪）
SFT_FAIL_ON_OVERSIZE = True

# LoRA 参数（与老师一致）
LORA_R = 16
LORA_ALPHA = 16
LORA_DROPOUT = 0.05
LORA_TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]

# 输出目录
SFT_OUTPUT_DIR = OUTPUT_ROOT / "qwen3_8b_sft"
SFT_LOG_DIR = LOG_ROOT / "qwen3_8b_sft"
MERGED_OUTPUT_DIR = OUTPUT_ROOT / "qwen3_8b_lora_merged"

# LoRA 合并：CPU 合并需要约 16GB 内存；GPU 机器可改成 "cuda"。
MERGE_DEVICE = "cpu"
MERGE_DTYPE = "bfloat16"


# =============================================================================
# ★ 5. DPO 数据与训练（07 / 08）
# =============================================================================
# ★ 07 采样候选使用的模型；默认用合并后的 SFT 模型。
DPO_GENERATION_MODEL = MERGED_OUTPUT_DIR
# ★ 可选：额外加入 SFT Bad Case 文件（JSONL，字段与 sft_train 相同）。
# 只能放 split="train" 的样本；07 会拒绝 validation/final_test 来源。
DPO_BAD_CASE_FILE: Path | None = None
# 硬指标同分时是否调用 LLM Judge 细排；默认关闭，避免 Judge 决定 chosen。
DPO_USE_LLM_JUDGE = False

# 每个 prompt 采样多少条候选报告
DPO_NUM_CANDIDATES = 6
DPO_CANDIDATE_TEMPERATURE = 0.9
DPO_CANDIDATE_TOP_P = 0.9
DPO_MAX_NEW_TOKENS = 2048
# 只保留硬指标（规则 F1 / 证据率 / 编造数 / 风险等级 / 语义准确率）有差异的样本
DPO_REQUIRE_HARD_DIFF = True
# 每条 prompt 最多重采样次数，凑不够不同候选就跳过
DPO_MAX_SAMPLE_ATTEMPTS = DPO_NUM_CANDIDATES * 3
# 最多构造多少个 prompt；None 表示全部
DPO_MAX_PROMPTS: int | None = None
DPO_VALIDATION_RATIO = 0.1
DPO_SEED = 42

DPO_EPOCHS = 1.0
DPO_BATCH_SIZE = 1
DPO_EVAL_BATCH_SIZE = 1
DPO_GRADIENT_ACCUMULATION_STEPS = 8
DPO_LEARNING_RATE = 5e-6
DPO_BETA = 0.1
# ★ 输入快照比老师项目长，prompt 提到 4096；报告 JSON 上限 2048。
DPO_MAX_PROMPT_LENGTH = 4096
DPO_MAX_COMPLETION_LENGTH = 2048
DPO_LOGGING_STEPS = 1
DPO_SAVE_STEPS = 50
DPO_EVAL_STEPS = 50
DPO_SAVE_TOTAL_LIMIT = 2

DPO_OUTPUT_DIR = OUTPUT_ROOT / "qwen3_8b_dpo_lora"
DPO_LOG_DIR = LOG_ROOT / "qwen3_8b_dpo_lora"
DPO_MERGED_OUTPUT_DIR = OUTPUT_ROOT / "qwen3_8b_dpo_merged"


# =============================================================================
# ★ 6. 评估（05 / 06）
# =============================================================================
EVAL_MODEL_PATH = MERGED_OUTPUT_DIR          # 默认评估合并后的 SFT 模型
EVAL_MAX_NEW_TOKENS = 2048
EVAL_LIMIT: int | None = None                 # None 表示评估全部；调试时可设 10
# 说明：05/06 逐条生成，不按 batch 推理（评估集较小，避免 padding 影响结果）。
EVAL_OUTPUT_DIR = ROOT / "eval_results"
EVAL_DEVICE = "auto"                          # auto / cuda / cpu
EVAL_DTYPE = "bfloat16"                       # bfloat16 / float16 / float32

# 06 pairwise 评估时的第二个模型（DPO 合并模型）；None 表示只做 pointwise
EVAL_MODEL_B_PATH: Path | None = None

# 评估判定阈值（写进报告，不参与训练）
TARGET_JSON_VALID_RATE = 0.99
TARGET_EVIDENCE_RATE = 0.95
TARGET_FABRICATION_RATE = 0.02


# =============================================================================
# 以下为内部常量，一般不需要修改
# =============================================================================
SAMPLE_TYPES = ("review", "semantic", "reject")
SPLITS = ("train", "validation", "final_test")
# 拒答能力（refusal 维度）的四类边界。
REJECT_TYPES = ("parse_failed", "insufficient", "out_of_scope", "semantic_unknown")
RISK_ORDER = {"low": 1, "medium": 2, "high": 3}

STANDARD_RED_LINES = [
    "不得引用输入 rule_hits / semantic_candidates 之外的规则号",
    "不得编造条款原文或证据位置",
    "不得把缺失条款写成已约定",
    "不得重新计算阈值或改变规则命中结论",
    "解析失败时不得强行输出审查报告",
]

MISSING_EVIDENCE_TEXT = "未检索到相关条款"


def ensure_output_dirs() -> None:
    """创建脚本会写入的目录；重复调用安全。"""
    for path in (
        SEEDS_DIR,
        MANIFEST_DIR,
        SFT_DATA_DIR,
        FINAL_TEST_DIR,
        DPO_DATA_DIR,
        VARIANT_CONTRACTS_DIR,
        OUTPUT_ROOT,
        LOG_ROOT,
        EVAL_OUTPUT_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
