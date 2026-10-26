# atguigu/import_process/nodes/node_document_split.py
import json
import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodeDocumentSplit(NodeBase):
    """
    文档切分节点：智能文档切片，md->chunks
    """

    name = "node_document_split"


    def get_new_md_content(self,state: ImportGraphState):
        # 一、拿md文件，换行符要统一
        md_path = state.get("md_path")
        if not md_path:
            logger.error("md路径不存在")
            raise ValueError("md路径不存在")
        md_path_obj = Path(md_path)
        if not md_path_obj.exists():
            logger.error("md文件不存在")
            raise FileNotFoundError("md文件不存在")
        file_title = md_path_obj.stem
        if not file_title:
            logger.error("md文件名不存在")
            raise ValueError("md文件名不存在")
        with open(md_path_obj, "r", encoding="utf-8") as f:
            md_content = f.read()
        if not md_content:
            logger.error("md文件内容为空")
            raise ValueError("md文件内容为空")
        md_content = md_content.replace("\r\n", "\n").replace("\r", "\n")
        return md_content,file_title,md_path_obj

    def get_section_list(self,md_content,file_title):
        # 二、切行,跳过代码块，按标题合并内容
        line_list = md_content.split("\n")
        code_pattern = r"^(`{3,}|~{3,})"
        title_pattern = r'^\s*#{1,6}\s+.+'
        is_in_code = False
        marker = None
        current_idx = 0
        section_list = []
        for idx, line in enumerate(line_list):
            code_match = re.match(code_pattern, line)
            if code_match:
                if not is_in_code:
                    is_in_code = True
                    marker = code_match.group(1)
                    logger.info(f"开始代码块：{marker}")
                else:
                    if marker == code_match.group(1):
                        is_in_code = False
                        logger.info(f"结束代码块：{marker}")
                        marker = None
            if not is_in_code and re.match(title_pattern, line):
                if idx == 0:
                    content_list = line_list[idx]
                    section_content = "\n".join(content_list)
                    section_title = content_list[0] if re.match(title_pattern, content_list[0]) else "无标题"
                else:
                    content_list = line_list[current_idx:idx]
                    section_content = "\n".join(content_list)
                    section_title = content_list[0] if re.match(title_pattern, content_list[0]) else "无标题"
                section_dict = {
                    "file_title": file_title,
                    "section_title": section_title,
                    "content": section_content
                }
                section_list.append(section_dict)
                current_idx = idx
        section_list.append({
            "file_title": file_title,
            "section_title": line_list[current_idx],
            "content": "\n".join(line_list[current_idx:])
        })
        return section_list

    def get_chunks(self,section_list,file_title,md_path_obj):
        # 三、长切短合后备份
        max_length = 300
        overlap = 30
        final_section_list = []
        spliter = RecursiveCharacterTextSplitter(separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " "],
                                                 chunk_size=max_length,
                                                 chunk_overlap=overlap)
        for section in section_list:
            content = section.get("content")
            section_title = section.get("section_title")
            real_content = content[len(section_title):] if content.startswith("#") else content
            if len(real_content) < max_length:
                final_section_list.append({
                    **section,
                    "part": 0
                })
            if "<table" in real_content:
                final_section_list.append({
                    **section,
                    "part": 0
                })
            splite_chunk_list = spliter.split_text(real_content)
            for idx, chunk in enumerate(splite_chunk_list):
                final_section_list.append({
                    "file_title": file_title,
                    "section_title": section_title,
                    "content": section_title + "\n\n" + chunk,#mk文档标题与正文之间规范是\n\n
                    "part": idx
                })
        # with open(md_path_obj.parent / "chunk.json", "w", encoding="utf-8") as f:
        #     f.write(json.dumps(final_section_list))
        return  final_section_list

    def process(self, state: ImportGraphState):
        # 第一大步：获取md的内容、文件标题、路径对象
        md_content, file_title,md_path_obj = self.get_new_md_content(state)
        # 第二大步：对md内容进行按行切割，根据标题合并section，返回的是section列表
        section_list=self.get_section_list(md_content,file_title)
        # 第三大步：对section列表进行精细切分（长切短合）,得到chunks
        final_section_list=self.get_chunks(section_list,file_title,md_path_obj)
        return {"chunks":final_section_list,"file_title":file_title}

if __name__=="__main__":
    node=NodeDocumentSplit()
    init_state={"md_path": r"E:\pdf_md\output\hak180产品安全手册\hak180产品安全手册_new.md"}
    result=node(init_state)
    logger.info(json_format(result))
