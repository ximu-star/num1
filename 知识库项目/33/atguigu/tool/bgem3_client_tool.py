from pymilvus.model.hybrid import BGEM3EmbeddingFunction

from atguigu.config.config import EmbeddingConfig
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger

bge_m3_model=None
def get_bge_m3_model():
    global bge_m3_model
    if not bge_m3_model:
        bge_m3_model = BGEM3EmbeddingFunction(
            model_name=EmbeddingConfig.bge_m3_path,
            device=EmbeddingConfig.bge_device,
            use_fp16=EmbeddingConfig.bge_fp16
        )
    return bge_m3_model

def get_bge_m3_embedding(text):
    bge_m3_model=get_bge_m3_model()
    embedding=bge_m3_model.encode_documents(text)
    # for sparse_item in embedding.get("sparse"):
    #     print(dict(zip(sparse_item.data,sparse_item.indices)))
#embedding:{'dense': [array([-0.00426305, -0.00426305,  ..., -0.00426305],shape=(1024,),dtype=float32)
#                     array([-0.00660303,  0.05431968, ..., -0.00874203],shape=(1024,),dtype=float32)],
#        'sparse': <Compressed Sparse Row sparse array of dtype 'float64' with 2 stored elements and shape (2, 250002)>}
    return {"dense":[dense_item.tolist() for dense_item in embedding.get("dense")],
            "sparse": [dict(zip(sparse_item.indices.tolist(),sparse_item.data.tolist()))
                       for sparse_item in embedding.get("sparse")]}

if __name__ == '__main__':
    texts = ["1", "2"]
    result = get_bge_m3_embedding(texts)
    logger.info(json_format(result))