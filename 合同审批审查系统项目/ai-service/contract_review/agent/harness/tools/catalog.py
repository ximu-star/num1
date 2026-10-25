from dataclasses import dataclass
from enum import StrEnum

from langchain_core.tools import BaseTool

from contract_review.agent.harness.tools.contract import (
    download_attachment,
    get_approval_detail,
    list_pending_approvals,
    parse_contract,
    run_pg_rules,
    save_result,
    search_manual_semantic,
    write_comment,
)
from contract_review.agent.harness.tools.skill import load_skill


class ToolCategory(StrEnum):
    """定义工具返回的数据类别。"""

    BUSINESS = "BUSINESS"
    KNOWLEDGE = "KNOWLEDGE"
    GUIDANCE = "GUIDANCE"


@dataclass(frozen=True)
class ToolDefinition:
    """组合一个 Agent 工具及其数据类别。"""

    tool: BaseTool
    category: ToolCategory


class ToolCatalog:
    """集中保存 Agent 可以使用的工具定义。"""

    def __init__(self, definitions: tuple[ToolDefinition, ...]):
        self.definitions = {
            definition.tool.name: definition
            for definition in definitions
        }

    def get_agent_tools(self) -> list[BaseTool]:
        return [definition.tool for definition in self.definitions.values()]

    def get_tool_definition(self, tool_name: str) -> ToolDefinition:
        return self.definitions[tool_name]

    def get_tool_category(self, tool_name: str) -> ToolCategory:
        return self.get_tool_definition(tool_name).category


TOOL_CATALOG = ToolCatalog(
    (
        ToolDefinition(list_pending_approvals, ToolCategory.BUSINESS),
        ToolDefinition(get_approval_detail, ToolCategory.BUSINESS),
        ToolDefinition(download_attachment, ToolCategory.BUSINESS),
        ToolDefinition(parse_contract, ToolCategory.BUSINESS),
        ToolDefinition(run_pg_rules, ToolCategory.BUSINESS),
        ToolDefinition(search_manual_semantic, ToolCategory.KNOWLEDGE),
        ToolDefinition(save_result, ToolCategory.BUSINESS),
        ToolDefinition(write_comment, ToolCategory.BUSINESS),
        ToolDefinition(load_skill, ToolCategory.GUIDANCE),
    )
)
