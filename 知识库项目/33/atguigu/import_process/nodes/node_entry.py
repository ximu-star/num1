# atguigu/import_process/nodes/node_entry.py
from pathlib import Path

from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodeEntry(NodeBase):
    """
    入口节点：任务分发
    """

    name = "node_entry"

    def process(self, state: ImportGraphState):
        local_file_path=state.get('local_file_path','')
        if not local_file_path:
            logger.error("路径错误：路径不存在")
            raise ValueError("路径错误：路径不存在")
        local_file_path_obj=Path(local_file_path)
        if not local_file_path_obj.exists():
            logger.error("路径错误：路径内文件不存在")
            raise ValueError("路径错误：路径内文件不存在")
        file_title=local_file_path_obj.stem
        file_suffix=local_file_path_obj.suffix
        if file_suffix=='.pdf':
            return {"pdf_path":local_file_path,"file_title":file_title,
                    "is_pdf_read_enabled":True,"is_md_read_enabled":False}
        elif file_suffix=='.md':
            return {"md_path":local_file_path,"file_title":file_title,
                    "is_pdf_read_enabled":False,"is_md_read_enabled":True}
        else:
            logger.error("路径错误：不支持的文件类型")
            raise ValueError("路径错误：不支持的文件类型")

if __name__ == "__main__":
    node=NodeEntry()
    init_state = {"local_file_path": r"E:\pdf_md\input\hak180产品安全手册.pdf"}
    result=node(init_state)
    logger.info( json_format(result))