from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from contract_review.agent.harness.run.runtime import AgentExecutionState,AgentRuntimeContext

from contract_review.agent.harness.skills.catalog import SKILL_CATALOG
from contract_review.agent.harness.skills.definition import SkillCode,SkillDefinition
from contract_review.agent.harness.tools.output import ToolResult


@tool
async def load_skill(
        skill_code: Annotated[
            SkillCode,
            "与当前任务最匹配的合同审查技能编码"
        ],
        runtime: ToolRuntime[
            AgentRuntimeContext,
            AgentExecutionState
        ]
) -> Command:
    """加载合同审查技能并更新当前 Agent 的能力范围"""

    # 1. 从服务端技能目录读取固定定义
    skill = SKILL_CATALOG.get_skill(skill_code)

    # 2. 将技能说明转换成模型可以读取的统一结果
    result_json = ToolResult[SkillDefinition](
        success=True,
        code="SKILL_LOADED",
        message="合同审查技能已加载",
        data=skill
    ).model_dump_json()

    # 3. 保存当前技能并把加载结果追加到 Agent 消息轨迹
    return Command(
        update={
            "active_skill_code": skill_code,
            "messages": [
                ToolMessage(
                    content=result_json,
                    tool_call_id=runtime.tool_call_id
                )
            ]
        }
    )
