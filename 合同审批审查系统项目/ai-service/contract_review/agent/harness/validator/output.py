from pydantic import Field

from contract_review.agent.harness.tools.snapshot import ToolCallReader
from contract_review.agent.harness.validator.action import WriteActionValidator
from contract_review.agent.harness.validator.answer import AnswerValidator
from contract_review.agent.llm.output import AgentOutput


class ValidatedAgentOutput(AgentOutput):
    token_usage: dict[str, int] = Field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )


class AgentOutputValidator:
    """在写回前校验合同事实与写操作边界。"""

    def __init__(self, tool_call_reader: ToolCallReader):
        self.tool_call_reader = tool_call_reader
        self.answer_validator = AnswerValidator()
        self.write_action_validator = WriteActionValidator()

    async def validate(
        self,
        agent_output: AgentOutput,
        run_id: str,
    ) -> ValidatedAgentOutput:
        # 1. 读取当前 Run 已持久化的全部工具调用快照
        tool_calls = await self.tool_call_reader.read_by_run_id(run_id)

        # 2. 校验合同事实并处理业务失败降级
        checked = self.answer_validator.validate(agent_output, tool_calls)

        # 3. 校验回写评论这一写操作
        self.write_action_validator.validate(checked, tool_calls)

        # 4. 返回可信输出
        return ValidatedAgentOutput.model_validate(checked.model_dump())
