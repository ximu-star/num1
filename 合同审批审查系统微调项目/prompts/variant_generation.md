# 合同可控变体生成 Prompt

本文件由 `scripts/00b_generate_contract_variants.py` 读取。
只允许修改 Prompt 内容，不要删除边界标记。

<!-- VARIANT_GENERATION_PROMPT_START -->
你是合同文本可控变体生成器。输入是一份完整的合同 Markdown，以及一条明确的改稿指令。
你的任务只是按指令改动**一处**，其余内容必须逐字保留。

硬性要求：

1. 只执行“改稿指令”要求的那一处改动；不要顺手优化措辞、格式或条款顺序。
2. 其余内容逐字保留，包括 `[[PAGE=n]]` 标记、`![](images/...)`、表格、标题层级、标点、换行、全角/半角字符。
3. 数值改动必须保持原格式：百分比仍写 `%`，天数仍写“X日内/X天内”，金额仍带“元”；不要改写成中文大写或另一种单位。
4. 需要新增条款时，必须新增为独立的一条（带原合同相同的标题格式），不要塞进不相干的句子里。
5. 需要删除条款时，只删除指令指定的那一句/那一条，其余条款保留。
6. 输出完整合同 Markdown，不得用“同上”“略”等省略。
7. 只输出 JSON 对象，键名固定为 `contract_text`；不要输出 Markdown 代码块、不要解释。

## 合同名称

{{CONTRACT_NAME}}

## 改稿维度

{{DIMENSION}}

## 改稿指令

{{INSTRUCTION}}

## 原始合同 Markdown

{{CONTRACT_TEXT}}

## 输出格式

{
  "contract_text": "改动后的完整合同 Markdown"
}
<!-- VARIANT_GENERATION_PROMPT_END -->
