# atguigu/query_process/nodes/node_web_search_mcp.py
import asyncio
import json

from agents.mcp import MCPServerStreamableHttp

from atguigu.config.config import McpConfig
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger

class NodeWebSearchMcp(NodeBase):
    """
    节点功能，调用外部搜索引擎补充信息
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_web_search_mcp"

    #第一步：mcp的sdk调用要先配置好api_key和url的环境
    #第二步：粘贴网站上拿调用mcp工具的代码，改吧改吧
    async def mcp_run(self,query: str, limit: int = 10):
        # 1. MCP Server 地址
        mcp_url=McpConfig.mcp_base_url
        # 2. 如果 MCP Server 需要 Token
        token = McpConfig.api_key
        # 3. 创建 MCP Client
        async with MCPServerStreamableHttp(
                name="websearch",
                params={
                    "url": mcp_url,
                    "headers": {
                        "Authorization": f"Bearer {token}"
                    },
                    "timeout": 10,
                },
                cache_tools_list=True,
                max_retry_attempts=3,
                client_session_timeout_seconds=30
        ) as server:
            # 4. 调用 MCP Tool
            result = await server.call_tool(
                "bailian_web_search",
                arguments={
                    "query": query,
                    "count": limit
                }
            )
            # 5. 返回结果
            return result

    #第三步：从调用mcp工具得到的搜索结果里构造web搜索的结果列表
    def process(self, state: QueryGraphState):
        rewritten_query = state.get("rewritten_query")
        if not rewritten_query:
            logger.error("rewritten_query不能为空")
            raise ValueError("rewritten_query不能为空")
        # 调用mcp
        result = asyncio.run(self.mcp_run(rewritten_query))
        search_data = json.loads(result.content[0].text).get("pages")
        return {
            "web_search_docs":
                [{"title": item.get("title"),
                  "content": item.get("snippet"),
                  "url": item.get("url"),
                  "source": "web"} for item in search_data]
        }

if __name__ == "__main__":
    init_state = {
        "rewritten_query": "关于BrotherHAK180烫金机如何使用"
    }
    # 执行节点的业务调用
    node_web_search_mcp = NodeWebSearchMcp()
    result = node_web_search_mcp(init_state)
    logger.info(json_format(result))