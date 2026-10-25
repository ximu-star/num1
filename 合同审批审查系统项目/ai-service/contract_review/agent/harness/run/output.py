from typing import Any

from contract_review.agent.harness.validator.output import ValidatedAgentOutput
from contract_review.agent.llm.output import ReplyType
from contract_review.common.utils import get_uid
from contract_review.models.models import AgentRunState


class AgentRunOutPutMapper:
    """把通过校验的合同审查输出映射成 AgentRun 结果。"""

    @staticmethod
    def map(
        validated_result: ValidatedAgentOutput,
    ) -> tuple[AgentRunState, dict[str, Any]]:
        if validated_result.reply_type == ReplyType.DECLINE:
            return AgentRunState.COMPLETED, AgentRunOutPutMapper._build_output(
                validated_result
            )
        return AgentRunState.COMPLETED, AgentRunOutPutMapper._build_output(
            validated_result
        )

    @staticmethod
    def _build_output(
        validated_result: ValidatedAgentOutput,
    ) -> dict[str, Any]:
        return {
            "message_id": get_uid("msg"),
            "content": {
                "text": validated_result.comment_text or validated_result.summary_text,
                "reply_type": validated_result.reply_type,
                "overall_risk_level": validated_result.overall_risk_level,
                "summary_text": validated_result.summary_text,
                "risk_items": [
                    item.model_dump(mode="json") for item in validated_result.risk_items
                ],
                "focus_points": validated_result.focus_points,
            },
        }
