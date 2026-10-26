# atguigu/import_process/nodes/node_bge_embedding.py
import json

from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.bgem3_client_tool import get_bge_m3_embedding
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodeBGEEmbedding(NodeBase):
    """
    混合向量化节点：使用 BGE-M3 模型将文本转换为向量
    """

    name = "node_bge_embedding"

    def process(self, state: ImportGraphState):
        chunks=state.get("chunks")
        if not chunks:
            logger.error("chunks不能为空")
            raise ValueError("chunks不能为空")

        for i in range(0,len(chunks),3):
            chunk_key_list=chunks[i:i+3]
            chunk_key_content_list=[f"{chunk.get("item_name")}{chunk.get("content")}" for chunk in chunk_key_list]
            embedding=get_bge_m3_embedding(chunk_key_content_list)
            for idx, chunk in enumerate(chunk_key_list):
                chunk.update({"dense_vector": embedding.get("dense")[idx],
                              "sparse_vector": embedding.get("sparse")[idx]})

        # with open (r"E:\pdf_md\output\hak180产品安全手册\bge_embedding_chunk.json", "w", encoding="utf-8") as f:
        #     f.write(json_format(chunks))
        return {"chunks": chunks}


if __name__ == "__main__":
    node=NodeBGEEmbedding()
    with open(r"E:\pdf_md\output\hak180产品安全手册\item_name_chunk.json", "r", encoding="utf-8") as f:
        chunks=json.load(f)
    init_state={"chunks": chunks}
    result = node(init_state)
    logger.info(json_format(result))