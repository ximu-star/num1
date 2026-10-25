from typing import Annotated, Any
from urllib.parse import quote

from langchain.tools import ToolRuntime, tool

from contract_review.agent.harness.run.runtime import AgentRuntimeContext
from contract_review.agent.harness.tools.executor import ToolExecutor
from contract_review.agent.harness.tools.output import (
    ApprovalDetailData,
    ApprovalTaskData,
    AttachmentData,
    ParseData,
    ReviewResultData,
)
from contract_review.infrastructure.contract_review_client import ContractReviewClient


def _client(runtime: ToolRuntime[AgentRuntimeContext]) -> ContractReviewClient:
    return ContractReviewClient(runtime.context.access_token)


tool_executor = ToolExecutor()


@tool
async def list_pending_approvals(
    limit: Annotated[int, "本次拉取并返回的待办数量，建议 20-50"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """拉取飞书待审合同列表，并按 approval_code 去重入库。"""
    client = _client(runtime)

    async def request() -> object:
        await client.post_json("/internal/v1/approvals/pull")
        return await client.get_json(
            "/internal/v1/tasks",
            query_params={"limit": limit},
        )

    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="list_pending_approvals",
        arguments={"limit": limit},
        operation=request,
        output_schema=list[ApprovalTaskData],
    )


@tool
async def get_approval_detail(
    approval_code: Annotated[str, "飞书审批编号，例如 HT-2026-001"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """查询单个审批单的附件、解析结果、规则命中和审查结果。"""
    client = _client(runtime)
    encoded = quote(approval_code, safe="")

    async def request() -> object:
        return await client.get_json(f"/internal/v1/tasks/{encoded}")

    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="get_approval_detail",
        arguments={"approval_code": approval_code},
        operation=request,
        output_schema=ApprovalDetailData,
    )


@tool
async def download_attachment(
    approval_code: Annotated[str, "飞书审批编号"],
    attachment_id: Annotated[int, "审批详情中的附件 ID"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """确认附件已下载到存储，并返回附件元数据。"""
    client = _client(runtime)
    encoded = quote(approval_code, safe="")

    async def request() -> dict[str, Any]:
        response = await client.get_json(f"/internal/v1/tasks/{encoded}")
        data = response.get("data") or {}
        attachments = data.get("attachments") or []
        for item in attachments:
            if int(item.get("id", -1)) == attachment_id:
                return {
                    "success": True,
                    "code": "OK",
                    "message": "附件已下载",
                    "data": item,
                }
        return {
            "success": False,
            "code": "ATTACHMENT_NOT_FOUND",
            "message": "附件不存在",
            "data": None,
        }

    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="download_attachment",
        arguments={"approval_code": approval_code, "attachment_id": attachment_id},
        operation=request,
        output_schema=AttachmentData,
    )


@tool
async def parse_contract(
    approval_code: Annotated[str, "飞书审批编号"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """读取合同解析结果：基本信息、条款、切片和扫描件标记。"""
    client = _client(runtime)
    encoded = quote(approval_code, safe="")

    async def request() -> dict[str, Any]:
        response = await client.get_json(f"/internal/v1/tasks/{encoded}")
        data = response.get("data") or {}
        parse = data.get("parse")
        if parse is None:
            return {
                "success": False,
                "code": "PARSE_NOT_FOUND",
                "message": "合同尚未解析完成",
                "data": None,
            }
        return {
            "success": True,
            "code": "OK",
            "message": "解析结果读取成功",
            "data": parse,
        }

    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="parse_contract",
        arguments={"approval_code": approval_code},
        operation=request,
        output_schema=ParseData,
    )


@tool
async def run_pg_rules(
    approval_code: Annotated[str, "飞书审批编号"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """执行 PG 确定性规则审查，并返回命中记录与审查结果。"""
    client = _client(runtime)
    encoded = quote(approval_code, safe="")

    async def request() -> object:
        await client.post_json(
            "/internal/v1/reviews/trigger",
            {"approval_code": approval_code, "force": False},
        )
        return await client.get_json(f"/internal/v1/tasks/{encoded}")

    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="run_pg_rules",
        arguments={"approval_code": approval_code},
        operation=request,
        output_schema=ApprovalDetailData,
    )


@tool
async def search_manual_semantic(
    query: Annotated[str, "要检索的手册语义问题，例如违约金是否对等"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """检索手册语义参考。Milvus 未启用时返回空结果并提示降级。"""
    client = _client(runtime)

    async def request() -> dict[str, Any]:
        # 预留真实 Milvus 检索接口；当前 8001 未启用语义检索时直接降级。
        return {
            "success": True,
            "code": "MILVUS_DISABLED",
            "message": "Milvus 未启用，已降级为只跑 PG 确定性规则",
            "data": [],
        }

    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="search_manual_semantic",
        arguments={"query": query},
        operation=request,
        output_schema=list[dict],
    )


@tool
async def save_result(
    approval_code: Annotated[str, "飞书审批编号"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """读取已保存的审查结果（8001 在规则审查时已落库）。"""
    client = _client(runtime)
    encoded = quote(approval_code, safe="")

    async def request() -> dict[str, Any]:
        response = await client.get_json(f"/internal/v1/tasks/{encoded}")
        data = response.get("data") or {}
        result = data.get("result")
        if result is None:
            return {
                "success": False,
                "code": "RESULT_NOT_FOUND",
                "message": "审查结果尚未生成",
                "data": None,
            }
        return {
            "success": True,
            "code": "OK",
            "message": "审查结果读取成功",
            "data": result,
        }

    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="save_result",
        arguments={"approval_code": approval_code},
        operation=request,
        output_schema=ReviewResultData,
    )


@tool
async def write_comment(
    approval_code: Annotated[str, "飞书审批编号"],
    comment_text: Annotated[str, "要回写到飞书审批评论区的最终审查意见"],
    runtime: ToolRuntime[AgentRuntimeContext],
) -> str:
    """把审查意见回写到飞书审批评论区，重复回写会被 8001 拦截。"""
    client = _client(runtime)
    encoded = quote(approval_code, safe="")

    async def request() -> object:
        return await client.post_json(
            f"/internal/v1/tasks/{encoded}/comment",
            {
                "approval_code": approval_code,
                "comment_text": comment_text,
                "attachment_version": 1,
            },
        )

    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="write_comment",
        arguments={"approval_code": approval_code, "comment_text": comment_text},
        operation=request,
        output_schema=dict,
    )
