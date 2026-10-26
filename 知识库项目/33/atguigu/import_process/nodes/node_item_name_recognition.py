# atguigu/import_process/nodes/node_item_name_recognition.py
import json

from langchain.chat_models import init_chat_model
from pymilvus import DataType

from atguigu.config.config import LLMConfig, MilvusConfig
from atguigu.config.prompt import ITEM_NAME_SYSTEM_PROMPT, ITEM_NAME_USER_PROMPT_TEMPLATE
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.bgem3_client_tool import get_bge_m3_embedding
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import get_milvus_client


class NodeItemNameRecognition(NodeBase):
    """
    主体识别节点：主体识别与标签提取
    """
    name = "node_item_name_recognition"

    def get_chunks(self, state: ImportGraphState):
        chunks = state.get("chunks")
        if not chunks:
            logger.error("chunks为空")
            raise ValueError("chunks为空")
        file_title = state.get("file_title", "")
        if not file_title:
            logger.error("file_title为空")
            raise ValueError("file_title为空")
        return chunks, file_title

    def get_content_strs(self, chunks, file_title):
        chunk_key_list = chunks[:10]
        max_len = 10000
        content_strs = "\n"
        for idx, chunk in enumerate(chunk_key_list, start=1):
            section_title = chunk.get("section_title", "")
            part = chunk.get("part", "")
            content = chunk.get("content", "")
            chunk_str = f"[切片{idx}]\n{file_title}\n{section_title}\n{part}\n{content}"
            content_strs += chunk_str
            if len(content_strs) > max_len:
                content_strs = content_strs[:max_len]
                logger.info(f"内容太长，已截断")
                break
        return content_strs

    def get_item_name(self,file_title,content_strs):
        llm = init_chat_model(
            model=LLMConfig.llm_model,
            model_provider="openai",
            temperature=LLMConfig.llm_temperature,
            api_key=LLMConfig.openai_api_key,
            base_url=LLMConfig.openai_base_url
        )
        message = [{"role": "system", "content": ITEM_NAME_SYSTEM_PROMPT},
                   {"role": "user",
                    "content": ITEM_NAME_USER_PROMPT_TEMPLATE.format(file_title=file_title, context=content_strs)}]
        res = llm.invoke(input=message)
        item_name = res.content
        item_name = item_name.replace(" ", "").replace("\n", "").replace("\t", "")
        if not item_name:
            item_name = file_title
        return item_name

    def create_milvus_collection(self,item_name,file_title,chunks):
        milvus_client = get_milvus_client()
        if not milvus_client:
            logger.error("初始化milvus_client失败")
            raise ValueError("初始化milvus_client失败")
        collection_name = MilvusConfig.item_name_collection
        if not milvus_client.has_collection(collection_name):
            schema = milvus_client.create_schema(auto_id=True)
            schema.add_field(
                field_name="id",
                datatype=DataType.INT64,
                is_primary=True
            ).add_field(
                field_name="file_title",
                datatype=DataType.VARCHAR,
                max_length=100
            ).add_field(
                field_name="item_name",
                datatype=DataType.VARCHAR,
                max_length=100
            ).add_field(
                field_name="dense_vector",
                datatype=DataType.FLOAT_VECTOR,
                dim=1024
            ).add_field(
                field_name="sparse_vector",
                datatype=DataType.SPARSE_FLOAT_VECTOR
            )
            index_params = milvus_client.prepare_index_params()
            index_params.add_index(
                field_name="dense_vector",
                index_type="IVF_FLAT",#暴力检索
                metric_type="COSINE",
                params={"nlist": 128, "nprobe": 10}
            )
            index_params.add_index(
                field_name="sparse_vector",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="IP",
                params={"inverted_index_algo": "DAAT_MAXSCORE",
                        "normalized": True,
                        "quantization": "none"}
            )
            milvus_client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=index_params)
        milvus_client.load_collection(collection_name=collection_name)
        safe_item_name = item_name.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        filter_str = f"item_name=='{safe_item_name}'"
        milvus_client.delete(collection_name=collection_name, filter=filter_str)

        embedding = get_bge_m3_embedding([item_name])
        data = {"file_title": file_title,
                "item_name": item_name,
                "dense_vector": embedding.get("dense")[0],
                "sparse_vector": embedding.get("sparse")[0]}
        milvus_client.insert(collection_name=collection_name, data=data)
        for chunk in chunks:
            chunk.update({"item_name": item_name})
        # with open(r"E:\pdf_md\output\hak180产品安全手册\item_name_chunk.json", "w", encoding="utf-8") as f:
        #     f.write(json_format(chunks))
        return milvus_client

    def process(self, state: ImportGraphState):
    # 第一大步：获取上个节点返回的chunks
        chunks, file_title = self.get_chunks(state)
    # 第二大步：根据chunks去切10个切片，整理成字符串
        content_strs=self.get_content_strs(chunks, file_title)
    # 第三大步：将字符串交给大模型进行识别，得到主体名称，且添加到原chunks中
        item_name = self.get_item_name(file_title, content_strs)
    # 第四大步：创建collection
        milvus_client=self.create_milvus_collection(item_name, file_title, chunks)
    # 第五大步：把主体名称存库
        return {"item_name": item_name, "chunks": chunks}


if __name__ == "__main__":
    node = NodeItemNameRecognition()
    with open(r"E:\pdf_md\output\hak180产品安全手册\chunk.json", "r", encoding="utf-8") as f:
        chunks = json.load(f)
    init_state = {"chunks": chunks, "file_title": "hak180产品安全手册"}
    result = node(init_state)
    logger.info(json_format(result))
