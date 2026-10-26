# atguigu/query_process/nodes/node_item_name_confirm.py

import json

from langchain.chat_models import init_chat_model

from atguigu.config.config import LLMConfig, MilvusConfig
from atguigu.config.prompt import ITEM_NAME_EXTRACT_SYSTEM_PROMPT, ITEM_NAME_EXTRACT_TEMPLATE
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.bgem3_client_tool import get_bge_m3_embedding
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import create_reqs, search_hybrid
from atguigu.tool.mongo_client_tool import get_recent_history_list, add_or_update_history, update_item_names_and_query


class NodeItemNameConfirm(NodeBase):
    """
    节点功能：确认用户问题中的核心商品名称。
    """
    """
            节点逻辑
            :param state: 工作流状态对象
            :return: 更新后的状态对象
    """
    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_item_name_confirm"

    def get_history_content(self, state):
        # 第一步：确定拿哪个session_id中的query，拿历史记录中最近的10条消息的大串
        session_id = state.get("session_id")
        if not session_id:
            logger.error("session_id is None")
            raise ValueError("session_id is None")
        original_query = state.get("original_query")
        if not original_query:
            logger.error("original_query is None")
            raise ValueError("original_query is None")
        message_id = add_or_update_history(session_id, "user", original_query)
        history_list = get_recent_history_list(session_id, limit=10)
        history_content = ""
        for history in history_list:
            role = history.get("role")
            text = history.get("text")
            content = f"{role}:{text}\n"
            history_content += content
        return history_content, message_id, original_query, session_id

    def get_item_names(self, history_content, original_query):
        # 第二步：将整理好的字符串交给大模型进行识别，得到分析出的item_names和rewritten_query
        llm = init_chat_model(
            model=LLMConfig.item_model,
            model_provider="openai",
            api_key=LLMConfig.openai_api_key,
            base_url=LLMConfig.openai_base_url,
            temperature=LLMConfig.llm_temperature
        )
        message = [{"role": "system", "content": ITEM_NAME_EXTRACT_SYSTEM_PROMPT},
                   {"role": "user", "content": ITEM_NAME_EXTRACT_TEMPLATE.format(history_text=history_content,
                                                                                 original_query=original_query)}]
        res = llm.invoke(input=message)
        res_json = res.content
        if res_json.startswith("```json"):
            res_json = res_json.replace("```json", "").replace("```", "")
        res_dict = json.loads(res_json)
        item_names = res_dict.get("item_names")
        rewritten_query = res_dict.get("rewritten_query")
        if not item_names:
            item_names = []
        if not rewritten_query:
            rewritten_query = original_query
        return item_names, rewritten_query

    def get_final_search_item_names(self, item_names):
        # 第三步：将得到的item_names向量化，去向量库检索，对得到的结果进行整理，存入字典列表
        embedding = get_bge_m3_embedding(item_names)
        collection_name = MilvusConfig.item_name_collection
        final_search_item_names = []
        for idx, item_name in enumerate(item_names):
            dense_data = embedding.get("dense")[idx]
            sparse_data = embedding.get("sparse")[idx]
            reqs = create_reqs(dense_data, sparse_data, dense_anns_field="dense_vector",
                               sparse_anns_field="sparse_vector")
            result = search_hybrid(collection_name, reqs, ranker=(0.8, 0.2), limit=10, output_fields=["item_name"])
            search_item_names = [
                {"original_item_name": item_name,
                 "search_item_name": i.get("entity").get("item_name"),
                 "score": i.get("distance")}
                for i in result[0]
            ]
            final_search_item_names.extend(search_item_names)
        return final_search_item_names

    def align_item_names(self, final_search_item_names,rewritten_query):
        # 第四步：（对齐）字典列表中不同得分确定是否需要再次沟通确定主体，如果有高分直接确定item_names和rewritten_query，
        # 没有高分有中等分数的让客户进行再次确认，没有高分和中等分直接返回未识别到
        confirm_item_names = [item.get("search_item_name") for item in final_search_item_names if
                              item.get("score") > 0.8]
        option_item_names = [item.get("search_item_name") for item in final_search_item_names if
                             item.get("score") > 0.6 and item.get("score") <= 0.8]
        if confirm_item_names:
            final_item_names = confirm_item_names
            answer = ""
        elif option_item_names:
            final_item_names = []
            answer = f"请问：{'\n'.join(option_item_names)}，是否有您想阅读的书籍？"
        else:
            final_item_names = []
            # llm=init_chat_model(
            #     model=LLMConfig.llm_model,
            #     model_provider="openai",
            #     api_key=LLMConfig.openai_api_key,
            #     base_url=LLMConfig.openai_base_url,
            #     temperature=LLMConfig.llm_temperature
            # )
            # message=[{"role": "user", "content": f"用户的问题是{rewritten_query},请给出积极向上、关心人的回答，尽量不留痕迹的将主题引导到某个行业的物品"}]
            # res = llm.invoke(message)
            # answer = res.content
            answer = ""
        return answer, final_item_names

    def handler_history(self, answer, message_id, session_id):
        # 第五步：是否需要再次沟通《=》判断answer有没有值，有值则需要再次沟通，没有值则不需要再次沟通确定主体
        # 但不管有没有值，都要拿到id给历史记录回填item_names和rewritten_query
        if answer:
            message_id = add_or_update_history(session_id, "assistant", answer)
        history_list = get_recent_history_list(session_id, limit=10)
        ids = [history.get("id") for history in history_list]
        return ids, message_id

    def process(self, state: QueryGraphState):
        # 第一步：确定拿哪个session_id中的query，拿历史记录中最近的10条消息的大串
        history_content, message_id, original_query, session_id = self.get_history_content(state)
        # 第二步：将整理好的字符串交给大模型进行识别，得到分析出的item_names和rewritten_query
        item_names, rewritten_query = self.get_item_names(history_content, original_query)
        # 第三步：将得到的item_names向量化，去向量库检索，对得到的结果进行整理，存入字典列表
        answer = ""
        final_item_names = []
        if not item_names:
            answer = "未识别到您的意向呢，您可以再问问呢"
        else:
            final_search_item_names = self.get_final_search_item_names(item_names)
            # 第四步：（对齐）字典列表中不同得分确定是否需要再次沟通确定主体，如果有高分直接确定item_names和rewritten_query，
            # 没有高分有中等分数的让客户进行再次确认，没有高分和中等分直接返回未识别到
            answer, final_item_names = self.align_item_names(final_search_item_names,rewritten_query)
            # 第五步：是否需要再次沟通《=》判断answer有没有值，有值则需要再次沟通，没有值则不需要再次沟通确定主体
            # 但不管有没有值，都要拿到id给历史记录回填item_names和rewritten_query
            ids = self.handler_history(answer, message_id, session_id)
            if ids:
                update_item_names_and_query(ids, final_item_names, rewritten_query)
        return {"session_id": session_id,
                "message_id": message_id,
                "original_query": original_query,
                "answer": answer,
                "item_names": final_item_names,
                "rewritten_query": rewritten_query,
                "history": get_recent_history_list(session_id, limit=10)}


if __name__ == "__main__":
    # 模拟会话历史
    session_id = "test_001"  # 前端传过来的对话id
    add_or_update_history(session_id, "user", "怎么用。")
    add_or_update_history(session_id, "assistant", "您好。请问是哪个型号")
    add_or_update_history(session_id, "user", "万用表")
    add_or_update_history(session_id, "assistant", "具体有什么问题呢？")

    # 初始化图状态
    init_state = {
        "session_id": "test_001",
        "original_query": "咋用？"
    }

    # 创建节点对象
    node_item_name_confirm = NodeItemNameConfirm()
    # 执行节点的单元测试
    result = node_item_name_confirm(init_state)
    # 将返回的图状态进行json序列化
    logger.info(json_format(result))
