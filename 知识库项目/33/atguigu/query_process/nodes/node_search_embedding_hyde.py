# atguigu/query_process/nodes/node_search_embedding_hyde.py
import json

from langchain.chat_models import init_chat_model

from atguigu.config.config import LLMConfig, MilvusConfig
from atguigu.config.prompt import HYDE_PROMPT
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.bgem3_client_tool import get_bge_m3_embedding
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import create_reqs, search_hybrid


class NodeSearchEmbeddingHyde(NodeBase):
    """
    节点功能：HyDE (Hypothetical Document Embedding)
    先让 LLM 生成假设性答案，再对答案进行向量检索，提高召回率。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_search_embedding_hyde"

    def process(self, state: QueryGraphState):
    #第一步：拿搜索需要的主体名和改写后的问题
        item_names=state.get("item_names")
        rewritten_query=state.get("rewritten_query")
        if not item_names:
            logger.error("item_names is None")
            raise Exception("item_names is None")
        if not rewritten_query:
            logger.error("rewritten_query is None")
            raise Exception("rewritten_query is None")
    #第二步：调用大模型，拿到改写后问题的回答
        llm=init_chat_model(
            model=LLMConfig.item_model,
            model_provider="openai",
            api_key=LLMConfig.openai_api_key,
            base_url=LLMConfig.openai_base_url,
            temperature=LLMConfig.llm_temperature
        )
        message=[{"role": "user", "content": HYDE_PROMPT.format(rewritten_query=rewritten_query)}]
        res=llm.invoke(input=message)
    #第三步：构造新问题，即原问题加假设性答案，对新问题进行向量化，去向量库进行混合检索
        hyde_query = f"{rewritten_query}{res.content}"
        embedding=get_bge_m3_embedding([hyde_query])
        dense_embedding=embedding.get("dense")[0]
        sparse_embedding=embedding.get("sparse")[0]
        item_names=[item.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
                    for item in item_names]
        expr=f"item_name in {json.dumps(item_names)}"
        reqs=create_reqs(dense_data=dense_embedding, sparse_data=sparse_embedding,
                         dense_anns_field="dense_vector",sparse_anns_field="sparse_vector",
                         limit=10, expr=expr)
        collection_name=MilvusConfig.milvus_collection
        result=search_hybrid(collection_name=collection_name,
                             reqs=reqs,
                             ranker=(0.8,0.2),
                             limit=10,
                             output_fields=["id","file_title", "section_title", "item_name", "content"])
    #第四步：构造最终需要的hyde_embedding_chunk的列表
        hyde_embedding_chunks =[{**item.get("entity",""),"score":item.get("distance"),"source":"local"}
                                for item in result[0]]
        return {"hyde_embedding_chunks": hyde_embedding_chunks}

if __name__ == "__main__":
    init_state = {
        "rewritten_query": "关于BrotherHAK180烫金机如何使用",
        "item_names": ["BrotherHAK180烫金机"]
    }
    node_search_embedding_hyde = NodeSearchEmbeddingHyde()
    result = node_search_embedding_hyde(init_state)
    logger.info(json_format(result))
