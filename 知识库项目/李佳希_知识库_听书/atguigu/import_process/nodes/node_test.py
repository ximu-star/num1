# atguigu/import_process/nodes/node_test.py

import json
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.logger import logger


class NodeTest(NodeBase):
    """
    节点功能：测试
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_test"

    def process(self, state: ImportGraphState) -> ImportGraphState:

        logger.info(f"【{self.name}】节点逻辑")

        return state

if __name__ == "__main__":

    # 初始化图状态
    init_state = {"local_file_path": r"E:\pdf_md\input\hak180产品安全手册.pdf"}

    # 创建节点对象
    node_test = NodeTest()
    # 执行节点的单元测试
    result = node_test(init_state)
    # 将返回的图状态进行json序列化
    json_state = json.dumps(result, ensure_ascii=False, indent=4)
    # 输出
    logger.info(json_state)
