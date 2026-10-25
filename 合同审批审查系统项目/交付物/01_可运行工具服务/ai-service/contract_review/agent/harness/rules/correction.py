from langchain_core.messages import SystemMessage

from contract_review.agent.harness.errors import (
    AgentOutputValidationError,
    CorrectableErrorCode,
)


class OutputCorrectionRules:
    """定义服务端输出校验失败后的模型纠错规则。"""

    @staticmethod
    def can_correct(error: AgentOutputValidationError) -> bool:
        """仅允许可纠正输出错误码进入模型纠错。"""
        return isinstance(error.code, CorrectableErrorCode)

    @staticmethod
    def build_feedback(error: AgentOutputValidationError) -> SystemMessage:
        """将可纠正错误转换成下一次模型调用的反馈消息。"""
        return SystemMessage(
            content=(
                "上一次输出未通过服务端校验："
                f"{error}。请仅根据成功工具结果重新生成；"
                "风险点必须引用 rule_hits 命中记录，"
                "无法确认的金额、比例、天数不要写入结论。"
            )
        )
