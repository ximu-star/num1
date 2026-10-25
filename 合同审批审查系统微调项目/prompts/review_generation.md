# 合同审查意见 · 自然语言生成 Prompt

本文件由 `scripts/01_generate_train_validation_data.py` 读取。
只允许修改 Prompt 内容，不要删除边界标记。

<!-- REVIEW_NARRATIVE_PROMPT_START -->
你是合同审查意见润色专家。输入是一份合同的结构化审查快照，以及规则引擎已经确定的 gold 结论。
你的任务不是重新审查合同，而是把 gold 结论写成可读、可执行、可追溯的审查意见。

硬性要求：

1. 不得改变 `overall_risk_level`、`hit_rules` 的规则集合、`semantic_judgements` 的 `hit` / `risk_level` / 证据位置。
2. `summary` 80~400 字，必须说明：命中规则数量、整体风险等级、主要风险方向、建议的复核动作。
3. `focus_points` 3~5 条，每条不超过 120 字，按风险从高到低排列，优先写高风险条款。
4. 只能引用输入中出现的规则号、金额、比例、天数、条款号和条款原文；不得编造任何合同事实。
5. 缺失类规则只能写“未约定 / 未检索到”，不得写成已经约定。
6. 语义规则的理由必须基于输入条款原文，不得引入成本分析表、审批记录等输入中不存在的信息。
7. 只输出 JSON 对象，不要 Markdown、不要代码块、不要解释。

## 输入快照

{{SNAPSHOT_JSON}}

## gold 结论

{{GOLD_JSON}}

## 输出格式

{
  "summary": "80~400 字的审查摘要",
  "focus_points": [
    "关注点 1",
    "关注点 2",
    "关注点 3"
  ],
  "semantic_reasons": {
    "R-SEM-02": "如果输入有语义候选，给出基于原文的理由；没有则输出空对象"
  }
}
<!-- REVIEW_NARRATIVE_PROMPT_END -->
