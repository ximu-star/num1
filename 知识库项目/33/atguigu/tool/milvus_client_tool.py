from pymilvus import MilvusClient, AnnSearchRequest, WeightedRanker

from atguigu.config.config import MilvusConfig
from atguigu.tool.logger import logger

milvus_client=None
def get_milvus_client():
    global milvus_client
    if not milvus_client:
        try:
            milvus_client=MilvusClient(uri=MilvusConfig.milvus_url)
            milvus_client.list_collections()
            logger.info("Milvus 轻量化客户端初始化成功")
        except Exception as e:
            logger.error(f"Milvus创建失败：{e}")
            raise e
    return milvus_client

def create_reqs(dense_data, sparse_data, dense_anns_field=None, sparse_anns_field=None, limit=None, expr=None, dense_params=None, sparse_params=None):
    if not dense_params:
        dense_params={"metric_type":"COSINE"}
    if not sparse_params:
        sparse_params={"metric_type":"IP"}
    dense_req=AnnSearchRequest(data=[dense_data],
                               anns_field=dense_anns_field,
                               param=dense_params,
                               limit=limit,
                               expr=expr)
    sparse_req=AnnSearchRequest(data=[sparse_data],
                                anns_field=sparse_anns_field,
                                param=sparse_params,
                                limit=limit,
                                expr=expr)
    return [dense_req, sparse_req]

def search_hybrid(collection_name, reqs, ranker=(0.8,0.2), limit=10, output_fields=None):
    milvus_client=get_milvus_client()
    weight_ranker=WeightedRanker(*ranker,norm_score=True)
    res=milvus_client.hybrid_search(
        collection_name=collection_name,
        reqs=reqs,
        ranker=weight_ranker,
        limit=limit,
        output_fields=output_fields)
    return res


