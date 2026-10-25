from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReplyType(StrEnum):
    """Agent 本轮生成的回复类型。"""

    ANSWER = "ANSWER"
    DECLINE = "DECLINE"


class RiskItem(BaseModel):
    """一条可回指 rule_hits 的风险点。"""

    model_config = ConfigDict(extra="forbid")

    rule_code: str = Field(min_length=1, description="命中规则的 rule_code")
    rule_name: str = Field(min_length=1, description="规则名称")
    risk_level: Literal["low", "medium", "high"] = Field(description="风险等级")
    evidence_text: str = Field(min_length=1, description="命中原文")
    evidence_position: str = Field(min_length=1, description="条款或页码位置")
    suggestion_text: str = Field(default="", description="处理建议")


class AgentOutput(BaseModel):
    """定义大语言模型必须返回的合同审查结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    reply_type: ReplyType = Field(
        default=ReplyType.ANSWER,
        description="本轮输出是正常审查结论还是拒绝/降级",
    )
    overall_risk_level: Literal["low", "medium", "high"] = Field(
        description="整体风险等级"
    )
    summary_text: str = Field(
        min_length=1,
        max_length=2000,
        description="中文风险摘要",
    )
    risk_items: list[RiskItem] = Field(
        default_factory=list,
        description="风险点列表，每项必须引用 rule_hits 中的 rule_code",
    )
    focus_points: list[str] = Field(
        default_factory=list,
        description="审批关注点",
    )
    comment_text: str = Field(
        min_length=1,
        max_length=3000,
        description="准备回写飞书评论区的完整文本",
    )

    @model_validator(mode="after")
    def validate_output_fields(self) -> Self:
        # 1. 有风险点时必须给出整体风险等级和评论
        if self.risk_items and not self.comment_text.strip():
            raise ValueError("存在风险点时必须提供 comment_text")

        # 2. 高风险结论必须至少有一条高风险风险点支撑
        if self.overall_risk_level == "high" and not any(
            item.risk_level == "high" for item in self.risk_items
        ):
            raise ValueError("整体高风险必须至少有一条高风险风险点")

        # 3. 返回通过字段组合校验的结果
        return self
