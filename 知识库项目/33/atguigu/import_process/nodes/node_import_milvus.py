# atguigu/import_process/nodes/node_import_milvus.py
import json

from pymilvus import DataType

from atguigu.config.config import MilvusConfig
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import milvus_client, get_milvus_client


class NodeImportMilvus(NodeBase):
    """
    导入向量库节点：数据持久化
    """

    name = "node_import_milvus"

    def get_chunks(self, state: ImportGraphState):
        chunks=state.get("chunks")
        if not chunks:
            logger.error("No chunks to import")
            raise Exception("No chunks to import")
        file_title = chunks[0].get("file_title")
        return chunks, file_title

    def create_milvus_collection(self, chunks, file_title):
        milvus_client=get_milvus_client()
        if not milvus_client:
            logger.error("No milvus client")
            raise Exception("No milvus client")
        collection_name=MilvusConfig.milvus_collection
        if not milvus_client.has_collection(collection_name):
            schema=milvus_client.create_schema(
                auto_id=True
            )
            schema.add_field(
                field_name="id",
                datatype=DataType.INT64,
                is_primary=True
            ).add_field(
                field_name="file_title",
                datatype=DataType.VARCHAR,
                max_length=100
            ).add_field(
                field_name="section_title",
                datatype=DataType.VARCHAR,
                max_length=100
            ).add_field(
                field_name="content",
                datatype=DataType.VARCHAR,
                max_length=5000
            ).add_field(
                field_name="part",
                datatype=DataType.INT64
            ).add_field(
                field_name="item_name",
                datatype=DataType.VARCHAR,
                max_length=100
            ).add_field(
                field_name="dense_vector",
                datatype=DataType.FLOAT_VECTOR,
                dim=len(chunks[0].get("dense_vector"))
            ).add_field(
                field_name="sparse_vector",
                datatype=DataType.SPARSE_FLOAT_VECTOR
            )

            index_params=milvus_client.prepare_index_params()
            index_params.add_index(
                field_name="dense_vector",
                index_type="IVF_FLAT",
                metric_type="COSINE",
                params={"nlist": 128, "nprobe": 10}
            )
            index_params.add_index(
                field_name="sparse_vector",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="IP",  # 距离相似度
                params={"inverted_index_algo": "DAAT_MAXSCORE",
                    # 高效的稀疏检索算法
                    "normalize": True,
                    # ↑ L2 归一化，让内积 (IP) 等价于余弦相似度
                    "quantization": "none"}
                # ↑ 关闭量化，保持原始精度：模型生成的向量已经压缩的一半的精度了（BGE_FP16=1），这里就不再压缩了
                # "quantization": "none" → 存储原始向量，不压缩
                # "quantization": "sq8" → 存储压缩后的向量（8-bit 量化
            )
            milvus_client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=index_params
            )
        milvus_client.load_collection(collection_name=collection_name)
        file_title=file_title.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        filter=f"file_title=='{file_title}'"
        milvus_client.delete(collection_name=collection_name, filter=filter)
        res=milvus_client.insert(collection_name=collection_name, data=chunks)
        logger.info(res)
        return res

    def update_chunk_with_id(self, chunks, res):
        ids=res.get("ids")
        for i,chunk in enumerate(chunks):
            chunk["id"]=ids[i]
        return chunks

    def process(self, state: ImportGraphState):
        chunks, file_title=self.get_chunks(state)
        res=self.create_milvus_collection(chunks, file_title)
        chunks=self.update_chunk_with_id(chunks, res)
        return {"chunks": chunks}



if __name__ == '__main__':
    node = NodeImportMilvus()
    with open(r"E:\pdf_md\output\hak180产品安全手册\bge_embedding_chunk.json","r",encoding="utf-8") as f:
        chunks = json.load(f)
    init_state = {"chunks": chunks}
    result = node(init_state)
    logger.info(json_format(result))