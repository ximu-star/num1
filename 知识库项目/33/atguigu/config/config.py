import os

from dotenv import load_dotenv

load_dotenv()

class MineruConfig:
    mineru_token=os.getenv('MINERU_TOKEN')
    mineru_base_url=os.getenv('MINERU_URL_upload')
    mineru_base_url_down=os.getenv('MINERU_URL_download')

class LLMConfig:
    llm_model=os.getenv('LLM_MODEL')
    vl_model=os.getenv('VL_MODEL')
    llm_temperature=os.getenv('LLM_TEMPERATURE')
    item_model=os.getenv('ITEM_MODEL')
    openai_api_key = os.getenv('OPENAI_API_KEY_ali')
    openai_base_url = os.getenv('OPENAI_BASE_URL_ali')

class MinIoConfig:
    minio_endpoint = os.getenv("MINIO_ENDPOINT")
    minio_access_key = os.getenv("MINIO_ACCESS_KEY")
    minio_secret_key = os.getenv("MINIO_SECRET_KEY")
    minio_bucket_name = os.getenv("MINIO_BUCKET_NAME")
    minio_img_dir = os.getenv("MINIO_IMG_DIR")

class EmbeddingConfig:
    bge_m3_path=os.getenv('BGE_M3_PATH')
    bge_m3 = os.getenv("BGE_M3")
    bge_device = os.getenv("BGE_DEVICE")
    # 特殊处理：将.env中的1/0转为布尔值，兼容常见的数字/字符串格式
    bge_fp16 = os.getenv("BGE_FP16") in ("1", "True", "true", 1)

class MilvusConfig:
    milvus_url = os.getenv("MILVUS_URL")
    milvus_collection = os.getenv("CHUNKS_COLLECTION")
    item_name_collection = os.getenv("ITEM_NAME_COLLECTION")

class MongoConfig:
    # mongo 服务地址
    mongo_url = os.getenv('MONGO_URL')
    mongo_db_name = os.getenv('MONGO_DB_NAME')

class McpConfig:
    mcp_base_url=os.getenv("MCP_DASHSCOPE_BASE_URL")
    api_key=os.getenv("OPENAI_API_KEY_ali")

class RerankConfig:
    rerank_base_url = os.getenv("RERANK_BASE_URL")
    rerank_api_key = os.getenv("RERANK_API_KEY")