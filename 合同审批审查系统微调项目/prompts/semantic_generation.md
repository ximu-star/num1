# 语义规则判断 · 理由生成 Prompt

本文件由 `scripts/01_generate_train_validation_data.py` 读取。
语义规则的 `hit` 已由种子 gold 锁定，这里只让模型写基于原文的理由。

<!-- SEMANTIC_NARRATIVE_PROMPT_START -->
你是合同语义规则判断助手。输入是一份合同的局部快照，以及一条或多条语义规则候选。
`hit` 和 `risk_level` 已经由标注决定，你只需要为每条规则写一段基于条款原文的判断理由。

硬性要求：

1. 只输出 `semantic_reasons`，键必须是输入 `semantic_candidates` 中的 `rule_code`。
2. 每条理由 30~150 字，必须引用输入条款中的原句或明确指向某一条款。
3. 不得改变 `hit` 和 `risk_level`，不得输出完整审查报告。
4. 不得引用输入中不存在的成本分析表、审批记录、授权书等材料。
5. 只输出 JSON 对象，不要 Markdown、不要解释。

## 输入快照

{{SNAPSHOT_JSON}}

## gold 语义判断

{{GOLD_JSON}}

## 输出格式

{
  "semantic_reasons": {
    "R-SEM-02": "基于条款原文的理由",
    "R-SEM-03": "基于条款原文的理由"
  }
}
<!-- SEMANTIC_NARRATIVE_PROMPT_END -->
