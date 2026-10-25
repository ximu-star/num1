# atguigu/import_process/nodes/node_md_img.py
import base64
import os
import re
import time
from collections import deque
from pathlib import Path

from langchain.chat_models import init_chat_model
from minio.deleteobjects import DeleteObject

from atguigu.config.config import LLMConfig, MinIoConfig
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.minio_client_tool import get_minio_client


class NodeMDImg(NodeBase):
    """
    MarkDown图片处理节点：多模态图片理解
    拿图片的摘要和url
    """

    name = "node_md_img"

    def get_md_images(self, state: ImportGraphState):
        # 1、拿md路径
        md_path = state.get('md_path', '')
        if not md_path:
            logger.error("路径错误：md路径不存在")
            raise ValueError("路径错误：md路径不存在")
        # 2、拿md路径md文件
        md_path_obj = Path(md_path)
        if not md_path_obj.exists():
            logger.error("路径错误：md路径内不存在md文件")
            raise FileNotFoundError("路径错误：md路径内不存在md文件")
        # 3、拿md文件内容
        # with open(md_path_obj, "r", encoding="utf-8") as f:
        #     md_content = f.read()
        md_content = md_path_obj.read_text(encoding="utf-8")
        if not md_content:
            logger.error("路径错误：md文件内容为空")
            raise ValueError("路径错误：md文件内容为空")
        # 4、拿images路径
        images_dir_path = md_path_obj.parent / "images"
        if not images_dir_path.exists():
            return md_content, md_path_obj, "", []
        # 5、拿所有图片名字的列表
        images_name_list = os.listdir(images_dir_path)  # 列出图片目录下的所有文件和文件夹的名字
        if not images_name_list:
            return md_content, md_path_obj, "", []
        return md_content, md_path_obj, images_dir_path, images_name_list

    def get_img_context(self, md_content, images_name_list, images_dir_path):
        IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        MAX_CONTEXT_LENGTH = 100
        img_context_list = []
        # 1、遍历图片名字列表
        for img_name in images_name_list:  # 每张图片的context
            # 2、确定格式是否正确
            if Path(img_name).suffix.lower() not in IMAGE_EXTENSIONS:
                logger.warning(f"图片格式不支持：{img_name}")
                continue
            # 3、正则找图片
            pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(img_name) + r"\)")
            match = pattern.search(md_content)
            if not match:
                logger.warning(f"图片未被引用：{img_name}")
                continue
            # 4、获取图片起始结束位置
            start, end = match.span()
            pre_context = md_content[max(0, start - MAX_CONTEXT_LENGTH):start]
            post_context = md_content[end:min(len(md_content), end + MAX_CONTEXT_LENGTH)]
            # 5、拿到所有图片的上下文列表
            img_path = str(images_dir_path / img_name)
            img_context_list.append({
                "img_name": img_name,
                "pre_context": pre_context,
                "post_context": post_context,
                "img_path": img_path
            })
        logger.info(f"所有图片的上下文列表已获取完成")
        return img_context_list

    def get_img_desc(self, img_context_list):
        # 1、大模型
        llm = init_chat_model(
            model=LLMConfig.vl_model,
            model_provider="openai",
            api_key=LLMConfig.openai_api_key,
            base_url=LLMConfig.openai_base_url,
            temperature=LLMConfig.llm_temperature
        )
        # 2、滑动门
        dq = deque(maxlen=10)
        current_time = time.time()
        for one_img_context in img_context_list:
            while dq and (current_time - dq[0] > 60):
                dq.popleft()
            if len(dq) == dq.maxlen:
                need_wait_time = 60 - (current_time - dq[0])
                if need_wait_time > 0:
                    time.sleep(need_wait_time)
                    current_time = time.time()
                    while dq and current_time - dq[0] > 60:
                        dq.popleft()
            dq.append(current_time)
            # 3、base64编码处理图片
            with open(one_img_context.get("img_path"), "rb") as f:
                image_bytes = f.read()
            base64_raw = base64.b64encode(image_bytes)
            base64_str = base64_raw.decode("utf-8")
            image_url = f"data:image/png;base64,{base64_str}"
            text = (f"这是一张图片，图片上文是：{one_img_context.get("pre_context")},"
                    f"图片下文是：{one_img_context.get("post_context")},请给我这张图片的摘要,要求30字内")
            messages = [
                {"role": "system", "content": "你是一个多模态图片理解专家"},
                {"role": "user",
                 "content":
                     [{"type": "image_url", "image_url": {"url": image_url}},
                      {"type": "text", "text": text}]
                 }
            ]
            # 4、调用大模型生成摘要
            response = llm.invoke(messages)
            one_img_context.update({"summary": response.content})
            one_img_context.pop("pre_context")
            one_img_context.pop("post_context")
        logger.info(f"摘要获取成功: {json_format(img_context_list)}")
        return img_context_list

    def img_url(self, img_context_list):
        # 1、连接minio客户端
        minio_client = get_minio_client()
        up_dir = MinIoConfig.minio_img_dir
        # 2、获取桶中原路径下的老照片
        old_img_list = minio_client.list_objects(bucket_name=MinIoConfig.minio_bucket_name,
                                                 prefix=up_dir,
                                                 recursive=True)
        # 3、批量删除老照片
        del_img_list = [DeleteObject(old_img.object_name) for old_img in old_img_list]
        errors=minio_client.remove_objects(bucket_name=MinIoConfig.minio_bucket_name, delete_object_list=del_img_list)
        for error in errors:
            logger.error(error)
        # 4、上传新照片,拿到新的url并保存
        for img_context in img_context_list:
            minio_client.fput_object(
                bucket_name=MinIoConfig.minio_bucket_name,
                object_name=up_dir +"/"+ img_context.get("img_name"),
                file_path=img_context.get("img_path")
            )
            url = f"http://{MinIoConfig.minio_endpoint}/{MinIoConfig.minio_bucket_name}/{up_dir}/{img_context.get('img_name')}"
            img_context.update({"url": url})
        logger.info(f"url获取成功: {json_format(img_context_list)}")
        return img_context_list

    def get_new_md_content(self, img_context_list, md_content, md_path_obj):
        for new_img in img_context_list:
            pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(new_img.get("img_name")) + r"\)")
            md_content = pattern.sub(lambda match: f"![{new_img.get('summary')}]({new_img.get('url')})", md_content)
        new_md_path_obj = md_path_obj.parent / str(md_path_obj.stem + "_new.md")
        with open(new_md_path_obj, "w", encoding="utf-8") as f:
            f.write(md_content)
        logger.info(f"新md文件已生成：{new_md_path_obj}")
        return new_md_path_obj, md_content

    def process(self, state: ImportGraphState):
        # 一、获取md文件内容和图片文件夹内的所有图片名字列表
        md_content, md_path_obj, images_dir_path, images_name_list = self.get_md_images(state)
        # 二、遍历图片名字列表，拿到内部所有图片的上下文列表
        img_context_list = self.get_img_context(md_content, images_name_list, images_dir_path)
        # 三、根据上下文和图片内容，调用大模型生成图片的描述摘要
        img_context_list = self.get_img_desc(img_context_list)
        # 四、拿到图片的线上url列表
        img_context_list = self.img_url(img_context_list)
        # 五、替换md当中的图片得到最终md文件
        new_md_path_obj, md_content = self.get_new_md_content(img_context_list, md_content, md_path_obj)
        return {"md_path":str(new_md_path_obj), "md_content":md_content}


if __name__ == "__main__":
    node = NodeMDImg()
    init_state = {"md_path": r"E:\pdf_md\output\hak180产品安全手册\hak180产品安全手册.md"}
    result = node(init_state)
    logger.info(json_format(result))
