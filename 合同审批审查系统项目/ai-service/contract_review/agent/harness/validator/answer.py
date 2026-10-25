from contract_review.agent.harness.errors import AgentOutputValidationError, CorrectableErrorCode
from contract_review.agent.harness.rules.fact import FactChecker
from contract_review.agent.harness.tools.catalog import TOOL_CATALOG, ToolCategory
from contract_review.agent.harness.tools.output import ToolFailureType
from contract_review.agent.harness.tools.snapshot import ToolCallSnapshot
from contract_review.agent.llm.output import AgentOutput, ReplyType


class AnswerValidationError(AgentOutputValidationError):
    """表示 Agent 回复不符合合同事实规则。"""


class AnswerValidator:
    """校验 Agent 审查结论使用的合同事实。"""

    business_failure_message = (
        "暂时无法取得可靠的合同解析或规则命中数据，"
        "因此现在不能确认审查结论，请稍后重试。"
    )

    def __init__(self):
        self.fact_checker = FactChecker()

    def validate(
        self,
        output: AgentOutput,
        tool_calls: tuple[ToolCallSnapshot, ...],
    ) -> AgentOutput:
        # 1. 筛选业务工具调用以及其中成功的结果
        business_calls = self._get_business_calls(tool_calls)
        successful_calls = self._get_successful_calls(business_calls)

        # 2. 全部业务调用失败时生成安全的降级回复
        if business_calls and not successful_calls:
            return self._build_failure_reply(output, business_calls)

        # 3. 使用成功工具结果校验摘要和评论中的合同事实
        self._validate_facts(
            f"{output.summary_text}\n{output.comment_text}",
            successful_calls,
        )
        return output

    @staticmethod
    def _get_business_calls(
        tool_calls: tuple[ToolCallSnapshot, ...],
    ) -> tuple[ToolCallSnapshot, ...]:
        return tuple(
            tool_call
            for tool_call in tool_calls
            if TOOL_CATALOG.get_tool_category(tool_call.tool_name)
            == ToolCategory.BUSINESS
        )

    @staticmethod
    def _get_successful_calls(
        business_calls: tuple[ToolCallSnapshot, ...],
    ) -> tuple[ToolCallSnapshot, ...]:
        return tuple(call for call in business_calls if call.success is True)

    def _build_failure_reply(
        self,
        output: AgentOutput,
        business_calls: tuple[ToolCallSnapshot, ...],
    ) -> AgentOutput:
        business_failures = tuple(
            call
            for call in business_calls
            if call.failure_type == ToolFailureType.BUSINESS
        )

        if len(business_failures) == len(business_calls):
            latest = business_failures[-1]
            message = (latest.result or {}).get("message") or self.business_failure_message
        else:
            message = self.business_failure_message

        return output.model_copy(
            update={
                "reply_type": ReplyType.DECLINE,
                "overall_risk_level": "low",
                "summary_text": message,
                "risk_items": [],
                "focus_points": ["请稍后重试；如持续失败，请转人工复核。"],
                "comment_text": message,
            }
        )

    def _validate_facts(
        self,
        reply_content: str,
        successful_calls: tuple[ToolCallSnapshot, ...],
    ) -> None:
        unsupported = self.fact_checker.get_unsupported_facts(
            reply_content,
            successful_calls,
        )
        if unsupported:
            values = "、".join(fact.value for fact in unsupported)
            raise AnswerValidationError(
                CorrectableErrorCode.UNSUPPORTED_FACT,
                f"结论中的合同事实缺少工具支持：{values}",
            )
