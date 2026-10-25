import asyncio

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage

from contract_review.agent.harness.dynamic_prompt import contract_prompt
from contract_review.agent.harness.run.runtime import (
    AgentExecutionState,
    AgentRuntimeContext,
)
from contract_review.agent.harness.skills.middleware import SkillScopeMiddleware
from contract_review.agent.harness.tools.catalog import TOOL_CATALOG
from contract_review.agent.llm.adapter import ModelAdapter
from contract_review.agent.llm.output import AgentOutput


def create_contract_review_agent():
    """创建合同审查 Agent；Harness 与 Skill 路由由中间件统一约束。"""
    return create_agent(  # type: ignore
        model=ModelAdapter.create_model(),
        tools=TOOL_CATALOG.get_agent_tools(),
        response_format=ToolStrategy(AgentOutput),
        state_schema=AgentExecutionState,
        context_schema=AgentRuntimeContext,
        middleware=[
            contract_prompt,
            SkillScopeMiddleware(),
            ToolCallLimitMiddleware(run_limit=10, exit_behavior="error"),
        ],
        name="合同审批审查助手",
    )


async def main_test():
    agent = create_contract_review_agent()
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="拉取待审合同并给出审查意见")]}
    )
    print(result["structured_response"])


if __name__ == "__main__":
    asyncio.run(main_test())
