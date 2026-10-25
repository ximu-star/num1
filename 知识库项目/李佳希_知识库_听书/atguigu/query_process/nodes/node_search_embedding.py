# atguigu/query_process/nodes/node_search_embedding.py
import json

from atguigu.config.config import MilvusConfig
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.bgem3_client_tool import get_bge_m3_embedding
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import create_reqs, search_hybrid


class NodeSearchEmbedding(NodeBase):
    """
    节点功能：基于已确认主体名+改写后的用户问题，执行Milvus向量数据库混合检索
    """

    #覆盖基类的 name 属性，标识节点名称
    name: str = "node_search_embedding"


    def process(self, state: QueryGraphState):
    #第一步：拿搜索需要的主体名和改写后的问题
        item_names = state.get("item_names")
        rewritten_query = state.get("rewritten_query")
        if not item_names:
            logger.error("item_names is None")
            raise Exception("item_names is None")
        if not rewritten_query:
            logger.error("rewritten_query is None")
            raise Exception("rewritten_query is None")
    #第二步：对问题进行向量化，去向量库进行混合检索
        embedding=get_bge_m3_embedding([rewritten_query])
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
    #第三步：构造最终需要的embedding_chunk的列表
        embedding_chunks =[{**item.get("entity",""),"score":item.get("distance"),"source":"local"}
                                for item in result[0]]
        return {"embedding_chunks": embedding_chunks}

if __name__ == "__main__":
    init_state = {
        "rewritten_query": "关于BrotherHAK180烫金机如何使用",
        "item_names": ["BrotherHAK180烫金机"]
    }
    node_search_embedding = NodeSearchEmbedding()
    result = node_search_embedding(init_state)
    logger.info(json_format(result))