# atguigu/query_process/nodes/node_answer_output.py
import re

from langchain.chat_models import init_chat_model

from atguigu.config.config import LLMConfig
from atguigu.config.prompt import ANSWER_PROMPT
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.mongo_client_tool import add_or_update_history
from atguigu.tool.task_utils import put_data


class NodeAnswerOutput(NodeBase):
    """
    节点功能: 答案生成
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_answer_output"

    def format_prompt(self, state):
        # 第二步：如果没有答案，构建提示词
        chunks = state.get("reranked_docs")
        chunk_content = ""
        for idx, chunk in enumerate(chunks, start=1):
            title = chunk.get("title")
            content = chunk.get("content")
            url = chunk.get("url")
            source = chunk.get("source")
            content = f"[{idx}][{source}] [{title}][{url}]\n{content}\n\n"
            chunk_content += content
        history = state.get("history")
        history_content = ""
        for h in history:
            role = h.get("role")
            text = h.get("text")
            content = f"{role}:{text}\n\n"
            history_content += content
        item_names = state.get("item_names")
        item_names_str = ",".join(item_names)
        rewritten_query = state.get("rewritten_query")
        prompt = ANSWER_PROMPT.format(
            context=chunk_content,
            history=history_content,
            item_names=item_names_str,
            question=rewritten_query
        )
        if len(prompt) > 10000:
            prompt = prompt[:10000]
        return chunks, item_names, prompt, rewritten_query

    def generate_answer(self, prompt, task_id):
        # 第三步：调大模型流式输出答案
        llm = init_chat_model(
            model=LLMConfig.vl_model,
            model_provider="openai",
            temperature=LLMConfig.llm_temperature,
            base_url=LLMConfig.openai_base_url,
            api_key=LLMConfig.openai_api_key
        )
        message = [{"role": "user", "content": prompt}]
        res = llm.stream(message)
        answer = ""  # 这个是完整的答案，要存储到state里面
        for r in res:
            # 流式输出，把答案放入队列，后续sse推送
            put_data(task_id, "delta", {"delta": r.content})
            answer += r.content
        return answer

    def get_image_urls(self, chunks):
        # 第四步：获取chunks当中图片url
        seen = set()  # 用于去重，避免同一张图片重复出现
        md_img_pattern = re.compile(r'!\[.*?\]\((.*?)\)')
        for i, doc in enumerate(chunks):
            # 检查 text 字段中的 Markdown 图片 (主要针对 Local Chunk)
            text = doc.get("content")
            matches = md_img_pattern.findall(text)  # 找所有的和正则匹配的元素放到列表
            for img_url in matches:
                img_url = img_url.strip()
                if img_url and img_url not in seen:
                    seen.add(img_url)
        images = list(seen)
        #   需要把这个答案变为历史记录存储mongo
        return images

    def write_history(self,answer, images, item_names, rewritten_query, state, task_id):
        # 第五步：把答案写入历史记录并且推送图片
        if answer:
            session_id = state.get("session_id")
            add_or_update_history(
                session_id=session_id,
                role="assistant",
                text=answer,
                rewritten_query=rewritten_query,
                item_names=item_names,
                image_urls=images
            )
        put_data(task_id, "final", {"image_urls": images})

    def process(self, state: QueryGraphState):
        #第一步：拿answer，如果在意图识别的时候已经得到答案，直接放队列，后期sse推送到前端
        answer=state.get("answer")
        task_id=state.get("task_id")
        if answer:
            # 如果在意图识别的时候已经得到答案，直接放队列，后期sse推送到前端
            put_data(task_id,"final", {"answer":answer})
        else:
            # 第二步：如果没有答案，构建提示词
            chunks, item_names, prompt, rewritten_query = self.format_prompt(state)
            # 第三步：调大模型流式输出答案
            answer = self.generate_answer(prompt, task_id)
            # 第四步：获取chunks当中图片url
            images = self.get_image_urls(chunks)
            # 第五步：把答案写入历史记录并且推送图片
            self.write_history(answer, images, item_names, rewritten_query, state, task_id)
        return {"answer":answer}