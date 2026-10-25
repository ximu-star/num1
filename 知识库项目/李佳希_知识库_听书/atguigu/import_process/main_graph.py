from langgraph.constants import END
from langgraph.graph import StateGraph

from atguigu.import_process.nodes.node_bge_embedding import NodeBGEEmbedding
from atguigu.import_process.nodes.node_document_split import NodeDocumentSplit
from atguigu.import_process.nodes.node_entry import NodeEntry
from atguigu.import_process.nodes.node_import_milvus import NodeImportMilvus
from atguigu.import_process.nodes.node_item_name_recognition import NodeItemNameRecognition
from atguigu.import_process.nodes.node_md_img import NodeMDImg
from atguigu.import_process.nodes.node_pdf_to_md import NodePDFToMD
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class MainGraphRunner:
    def __init__(self):
        self.builder = StateGraph(state_schema=ImportGraphState)
        self.add_nodes()
        self.add_edges()
        self.graph = None

    def add_nodes(self):
        self.builder.add_node(NodeEntry.name, NodeEntry())
        self.builder.add_node(NodePDFToMD.name, NodePDFToMD())
        self.builder.add_node(NodeMDImg.name, NodeMDImg())
        self.builder.add_node(NodeDocumentSplit.name, NodeDocumentSplit())
        self.builder.add_node(NodeItemNameRecognition.name, NodeItemNameRecognition())
        self.builder.add_node(NodeBGEEmbedding.name, NodeBGEEmbedding())
        self.builder.add_node(NodeImportMilvus.name, NodeImportMilvus())

    def add_edges(self):
        self.builder.set_entry_point(NodeEntry.name)
        self.builder.add_conditional_edges(NodeEntry.name, self.afer_entry_router)
        self.builder.add_edge(NodePDFToMD.name, NodeMDImg.name)
        self.builder.add_edge(NodeMDImg.name, NodeDocumentSplit.name)
        self.builder.add_edge(NodeDocumentSplit.name, NodeItemNameRecognition.name)
        self.builder.add_edge(NodeItemNameRecognition.name, NodeBGEEmbedding.name)
        self.builder.add_edge(NodeBGEEmbedding.name, NodeImportMilvus.name)
        self.builder.add_edge(NodeImportMilvus.name, END)

    def afer_entry_router(self, state: ImportGraphState):
        is_md_read_enabled = state.get('is_md_read_enabled', False)
        is_pdf_read_enabled = state.get('is_pdf_read_enabled', False)
        if is_md_read_enabled:
            return NodeMDImg.name
        elif is_pdf_read_enabled:
            return NodePDFToMD.name
        else:
            return END

    def run(self, state: ImportGraphState):
        if not self.graph:
            self.graph = self.builder.compile()
        return self.graph.invoke(state)

    @classmethod
    def create_and_run(cls, state):
        return cls().run(state)


if __name__ == "__main__":
    init_state = {"pdf_path": r"E:\pdf_md\input\万用表RS-12的使用.pdf",
                  "local_dir": r"E:\pdf_md\output",
                  "local_file_path": r"E:\pdf_md\local_file_path\万用表RS-12的使用.pdf"}
    result = MainGraphRunner.create_and_run(init_state)
    logger.info(json_format(result))
