from contract_review.agent.harness.errors import AgentOutputValidationError, CorrectableErrorCode
from contract_review.agent.harness.rules.action import MAX_COMMENT_LENGTH
from contract_review.agent.harness.tools.snapshot import ToolCallSnapshot
from contract_review.agent.llm.output import AgentOutput


class WriteActionValidationError(AgentOutputValidationError):
    """表示回写评论这一写操作不符合安全规则。"""


class WriteActionValidator:
    """校验回写评论前的写操作边界。"""

    def validate(
        self,
        output: AgentOutput,
        tool_calls: tuple[ToolCallSnapshot, ...],
    ) -> None:
        # 1. 评论必须非空
        if not output.comment_text.strip():
            raise WriteActionValidationError(
                CorrectableErrorCode.COMMENT_EMPTY,
                "回写评论不能为空",
            )

        # 2. 评论不能超过飞书评论长度上限
        if len(output.comment_text) > MAX_COMMENT_LENGTH:
            raise WriteActionValidationError(
                CorrectableErrorCode.COMMENT_TOO_LONG,
                f"回写评论超过 {MAX_COMMENT_LENGTH} 字",
            )

        # 3. 已经成功回写过时，服务端会直接跳过重复回写；
        #    此处只做提示，不修改已通过结构校验的评论内容。
        _ = any(
            call.tool_name == "write_comment"
            and call.success is True
            and (call.result or {}).get("code") == "OK"
            for call in tool_calls
        )
