import dashscope
from http import HTTPStatus

from modelscope.models.nlp.dgds.backbone import Rerank

from atguigu.config.config import RerankConfig
from atguigu.tool.logger import logger

# 以下为华北2（北京）地域的配置，调用时请将{WorkspaceId}替换为真实的业务空间ID，各地域的配置不同。
dashscope.base_http_api_url = RerankConfig.rerank_base_url
dashscope.api_key = RerankConfig.rerank_api_key

def text_rerank(query,texts,limit=10):
    try:
        resp = dashscope.TextReRank.call(
            model="qwen3-rerank",
            query=query,
            documents=texts,
            top_n=limit,
            return_documents=False,
            instruct="Given a web search query, retrieve relevant passages that answer the query."
        )
        if resp.status_code == HTTPStatus.OK:
            return [{"index": item.index, "score": item.relevance_score} for item in resp.output.results]
        else:
            logger.error("重排序请求有问题")
            raise Exception("重排序请求有问题")
    except Exception as e:
        logger.error(e)
        raise e


if __name__ == '__main__':
    print(text_rerank("你是谁", ["你是谁", "我是AI", "我是机器人"], 2))