# DPO 候选排序 · Judge Prompt（仅用于硬指标同分时的细排）

本文件由 `scripts/07_generate_dpo_data.py` 读取。

<!-- DPO_JUDGE_PROMPT_START -->
你是合同审查报告偏好数据评审专家。请依据输入快照和评分要点，对候选报告做从优到劣的完整排序。

评审优先级：

1. 规则集合：`hit_rules` 必须与输入 `rule_hits` 完全一致，不能漏规则，也不能新增规则。
2. 证据溯源：`evidence_text` 必须能在条款原文中找到；缺失类必须写“未检索到相关条款”。
3. 风险等级：`overall_risk_level` 必须等于命中规则和命中语义规则中的最高风险。
4. 语义判断：`semantic_judgements` 必须覆盖全部候选，判断与原文一致。
5. 真实性：不得出现输入之外的规则号、金额、比例、天数、条款号。
6. 可读性：摘要覆盖主要风险并有归纳，关注点可执行，不使用空话和模板腔。
7. 简洁性：在信息完整的前提下更简洁的更好，不允许只因更长判优。

只输出 JSON 对象，不要 Markdown。

## 输入快照

{{SNAPSHOT_JSON}}

## 评分要点

{{RUBRIC_JSON}}

## 候选报告

{{CANDIDATES_JSON}}

## 输出格式

本项目固定比较 2 条候选，按从优到劣输出索引：

{
  "ranking": [0, 1]
}
<!-- DPO_JUDGE_PROMPT_END -->
