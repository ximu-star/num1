from langgraph.constants import END
from langgraph.graph import StateGraph

from atguigu.query_process.nodes.node_answer_output import NodeAnswerOutput
from atguigu.query_process.nodes.node_item_name_confirm import NodeItemNameConfirm
from atguigu.query_process.nodes.node_rerank import NodeRerank
from atguigu.query_process.nodes.node_rrf import NodeRrf
from atguigu.query_process.nodes.node_search_embedding import NodeSearchEmbedding
from atguigu.query_process.nodes.node_search_embedding_hyde import NodeSearchEmbeddingHyde
from atguigu.query_process.nodes.node_web_search_mcp import NodeWebSearchMcp
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class QueryMainGraphRunner:
    def __init__(self):
        self.builder=StateGraph(state_schema=QueryGraphState)
        self.add_nodes()
        self.add_edges()
        self.graph=None

    def add_nodes(self):
        self.builder.add_node(NodeItemNameConfirm.name, NodeItemNameConfirm())
        self.builder.add_node(NodeWebSearchMcp.name, NodeWebSearchMcp())
        self.builder.add_node(NodeSearchEmbedding.name, NodeSearchEmbedding())
        self.builder.add_node(NodeSearchEmbeddingHyde.name, NodeSearchEmbeddingHyde())
        self.builder.add_node(NodeRrf.name, NodeRrf())
        self.builder.add_node(NodeRerank.name, NodeRerank())
        self.builder.add_node(NodeAnswerOutput.name, NodeAnswerOutput())

    def after_item_name_confirm_router(self, state: QueryGraphState):
        answer=state.get("answer","")
        if answer:
            return NodeAnswerOutput.name
        else:
            return [NodeSearchEmbedding.name, NodeSearchEmbeddingHyde.name,NodeWebSearchMcp.name]

    def add_edges(self):
        self.builder.set_entry_point(NodeItemNameConfirm.name)
        self.builder.add_conditional_edges(NodeItemNameConfirm.name, self.after_item_name_confirm_router)
        self.builder.add_edge(NodeSearchEmbedding.name, NodeRrf.name)
        self.builder.add_edge(NodeSearchEmbeddingHyde.name, NodeRrf.name)
        self.builder.add_edge(NodeWebSearchMcp.name, NodeRrf.name)
        self.builder.add_edge(NodeRrf.name, NodeRerank.name)
        self.builder.add_edge(NodeRerank.name, NodeAnswerOutput.name)
        self.builder.add_edge(NodeAnswerOutput.name, END)

    def run(self,state):
        if not self.graph:
            self.graph = self.builder.compile()
        return self.graph.invoke(state)

    @classmethod
    def create_and_run(cls, state):
        return cls().run(state)


if __name__ == '__main__':
    runner = QueryMainGraphRunner()
    init_state = {
        "answer": "haha",
    }
    result = runner.run(init_state)
    logger.info(json_format(result))
