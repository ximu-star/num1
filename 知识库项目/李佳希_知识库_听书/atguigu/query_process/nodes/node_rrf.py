# atguigu/query_process/nodes/node_rrf.py

from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger

class NodeRrf(NodeBase):
    """
    这个节点就是把向量搜索和假设性文档搜索两种方式的搜索结果进行加权融合rrf排序。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_rrf"

    def process(self, state: QueryGraphState):
    #第一步：拿到向量搜索的embedding_chunks和hyde_embedding_chunks
        embedding_chunks=state.get("embedding_chunks")
        hyde_embedding_chunks=state.get("hyde_embedding_chunks")
        if not embedding_chunks:
            logger.error("embedding_chunks不能为空")
            raise Exception("embedding_chunks不能为空")
        if not hyde_embedding_chunks:
            logger.error("hyde_embedding_chunks不能为空")
            raise Exception("hyde_embedding_chunks不能为空")
    #第二步：设权重，拿最终按总分排序的dict，首先要算每一路每一个chunks的分数，进行分数更新，要考虑不同路可能有相同的chunks
        weight_embedding=[(embedding_chunks,0.7),(hyde_embedding_chunks,0.3)]
        final_chunks_dict={}
        for chunks, weight in weight_embedding:
            for idx,chunk in enumerate(chunks,start=1):
                final_chunks_id=chunk.get("id")
                new_chunk_score=chunk.get("score")+weight/(idx+60)
                if not final_chunks_id in final_chunks_dict:
                    chunk["score"]=new_chunk_score
                    final_chunks_dict[final_chunks_id] = chunk
                else:
                    final_chunks_dict.get(final_chunks_id)["score"]+=new_chunk_score
        rrf_chunks=sorted(final_chunks_dict.values(),key=lambda x:x["score"],reverse=True)
        return {"rrf_chunks":rrf_chunks[:10]}



if __name__ == '__main__':
    mock_state = {
    "embedding_chunks": [
        {
            "content": "## HAK 180 烫金机\n\n产品安全手册（简体中文）\n\n感谢您购买 HAK 180 烫金机。\n\n在使用本设备之前，请先阅读本手册，包括所有预防措施。阅读本手册后，请妥善保管。\n\n有关使用本设备的更多信息，请参阅使用说明书，其可在兄弟 (中国)商业有限公司技术服务支持网站 http://www.95105369.com/Web/Manuals.aspx 上找到。建议您先通读使用说明书，再使用本设备。\n\n如需获得常见问题解答、故障排除和说明书，请访问\n\nhttp://www.95105369.com。\n\n对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。",
            "id": 468279113385596503,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## HAK 180 烫金机",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.8347936272621155,
            "source": "local"
        },
        {
            "content": "## 设备\n\n![设备内部高温，勿触灰区，待冷却再开盖，防烫伤。](http://192.168.10.128:9000/project/upload-images/f3349cded08d6686a93d0a81b9a64ec1e50d9a82cbb88541b37027f085813a15.jpg)  \n儎⑟ഴḽ䆜઀ᛞ࠽व䀜᪮儎⑟Ⲻ䇴༽䜞ԬȾ",
            "id": 468279113385596512,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.8297356367111206,
            "source": "local"
        },
        {
            "content": "## HAK 180 烫金机\n\n•\t对于保养、调整或维修事宜，请联系 Brother 呼叫中心或您当地的Brother 经销商。\n\n•\t如果本设备工作不正常或发生任何错误，请关闭本设备，拔下所有电缆，然后联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t本文档中提供的信息可能会随时更改，恕不另行通知。\n\n•\t严禁未经授权擅自复制或重制本文档的任何部分或全部内容。\n\n•\t请注意，对于使用通过本设备制作的产品造成的任何损坏或利润损失，或者故障、维修导致的数据消失或更改，或者第三方提出的任何索赔，我们不承担任何责任。",
            "id": 468279113385596504,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## HAK 180 烫金机",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.8282701373100281,
            "source": "local"
        },
        {
            "content": "## 设备\n\n![禁止将手指放入设备指定区域，确保操作安全，防止意外伤害。](http://192.168.10.128:9000/project/upload-images/5067b2891ca4f761e2874921e0eb433aa742afbf38ca8dc509afecbf0aa6a6b5.jpg)",
            "id": 468279113385596518,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.8232405185699463,
            "source": "local"
        },
        {
            "content": "## 设备\n\n•\t请勿拆解本设备。拆解本设备可能会导致火灾或触电。\n\n•\t请勿尝试自行维修本设备。打开或拆下盖子可能使您接触到危险电压点以及带来其他风险，并且可能使您的保修失效。对于所有维修事宜，请联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t请在以下环境使用本设备：温度保持在 10 °C 和 32 °C 之间，湿度保持在 20% 和 80% 之间，无冷凝。\n\n•\t请勿使本设备受到阳光直射、过热、接触明火、腐蚀性气体、湿气或灰尘。否则可能产生触电、短路或火灾的风险，从而导致损坏设备和/或导致设备无法运行。\n\n•\t请勿将设备放在加热器、空调、电风扇或水附近。",
            "id": 468279113385596508,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.8214895129203796,
            "source": "local"
        },
        {
            "content": "## 设备\n\n•\t请先阅读这本手册，再尝试操作本设备或尝试进行任何维护。不按照这些说明操作可能会提高发生人员受伤或财产损坏（包括火灾、触电、烧伤或窒息所致）的风险。对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。\n\n•\t请勿在未去除所有包装材料的情况下使用本设备，包括本设备内部的任何附加的包装材料。否则可能会产生火灾的风险。\n\n•\t请勿拆解本设备。拆解本设备可能会导致火灾或触电。",
            "id": 468279113385596507,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.8149500489234924,
            "source": "local"
        },
        {
            "content": "## 设备\n\n如果遵守了操作说明进行操作，但是设备不能正确运行，请仅调整操作说明中涵盖的控制。错误调整其他控制可能导致损坏并且通常需要合格技术进行全面工作以将本设备恢复到正常操作。Brother不建议使用 Brother 正品烫金膜盒以外的其他品牌烫金膜盒。如果使用与本设备不兼容的耗材导致损坏本设备的任何零件，由此导致的任何维修可能不在保修范围内。",
            "id": 468279113385596525,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.8090631365776062,
            "source": "local"
        },
        {
            "content": "## 设备\n\n•\t将本设备放置在平整、水平且稳定的表面上（如桌面），避免震动和冲击。\n\n•\t将本设备放置在通风良好的环境中。\n\n•\t为了防止人员受伤，请谨慎操作，避免将手指放置在图中所示的区域中。\n\n![禁止将手指伸入设备内部齿轮区域，以防受伤，需谨慎操作。](http://192.168.10.128:9000/project/upload-images/c61a7f4e923881679f747508ae309c39dc221685344b068009256b1b3a40cc00.jpg)",
            "id": 468279113385596517,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.7164815068244934,
            "source": "local"
        },
        {
            "content": "## 设备\n\n![手正操作打开的设备内部结构，展示其组件布局与安装方式。](http://192.168.10.128:9000/project/upload-images/501bb8d2d681e4502d87badb15a68939eadfa086d309c3599f1c36b0bc559177.jpg)",
            "id": 468279113385596513,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.6994372010231018,
            "source": "local"
        },
        {
            "content": "## 为设备选择一个安全的位置\n\n![正确搬运设备：双手抓两侧，勿提托板盒，防跌落伤人，确保平稳安全。](http://192.168.10.128:9000/project/upload-images/cc5ee1ac24ebb2707d40dc7a234a8b243f55f5bf08fabc683859be6fdf096ffa.jpg)  \n确保本设备的任何部位均未伸出设备所在的桌面或支架。特别是当本设备位于桌面、支架等边缘时，请勿让出纸盒打开。确保本设备位于平整、水平且稳定的表面上，避免震动。不遵守这些预防措施可能导致设备跌落，从而导致用户的人身伤害以及设备严重损坏。",
            "id": 468279113385596522,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 为设备选择一个安全的位置",
            "item_name": "BrotherHAK180烫金机",
            "score": 0.6990230083465576,
            "source": "local"
        }
    ],
        "hyde_embedding_chunks": [
        {
            "id": 468279113385596503,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## HAK 180 烫金机",
            "item_name": "BrotherHAK180烫金机",
            "content": "## HAK 180 烫金机\n\n产品安全手册（简体中文）\n\n感谢您购买 HAK 180 烫金机。\n\n在使用本设备之前，请先阅读本手册，包括所有预防措施。阅读本手册后，请妥善保管。\n\n有关使用本设备的更多信息，请参阅使用说明书，其可在兄弟 (中国)商业有限公司技术服务支持网站 http://www.95105369.com/Web/Manuals.aspx 上找到。建议您先通读使用说明书，再使用本设备。\n\n如需获得常见问题解答、故障排除和说明书，请访问\n\nhttp://www.95105369.com。\n\n对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。",
            "score": 0.8546956777572632,
            "source": "local"
        },
        {
            "id": 468279113385596504,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## HAK 180 烫金机",
            "item_name": "BrotherHAK180烫金机",
            "content": "## HAK 180 烫金机\n\n•\t对于保养、调整或维修事宜，请联系 Brother 呼叫中心或您当地的Brother 经销商。\n\n•\t如果本设备工作不正常或发生任何错误，请关闭本设备，拔下所有电缆，然后联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t本文档中提供的信息可能会随时更改，恕不另行通知。\n\n•\t严禁未经授权擅自复制或重制本文档的任何部分或全部内容。\n\n•\t请注意，对于使用通过本设备制作的产品造成的任何损坏或利润损失，或者故障、维修导致的数据消失或更改，或者第三方提出的任何索赔，我们不承担任何责任。",
            "score": 0.8443992137908936,
            "source": "local"
        },
        {
            "id": 468279113385596508,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "content": "## 设备\n\n•\t请勿拆解本设备。拆解本设备可能会导致火灾或触电。\n\n•\t请勿尝试自行维修本设备。打开或拆下盖子可能使您接触到危险电压点以及带来其他风险，并且可能使您的保修失效。对于所有维修事宜，请联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t请在以下环境使用本设备：温度保持在 10 °C 和 32 °C 之间，湿度保持在 20% 和 80% 之间，无冷凝。\n\n•\t请勿使本设备受到阳光直射、过热、接触明火、腐蚀性气体、湿气或灰尘。否则可能产生触电、短路或火灾的风险，从而导致损坏设备和/或导致设备无法运行。\n\n•\t请勿将设备放在加热器、空调、电风扇或水附近。",
            "score": 0.8424421548843384,
            "source": "local"
        },
        {
            "id": 468279113385596512,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "content": "## 设备\n\n![设备内部高温，勿触灰区，待冷却再开盖，防烫伤。](http://192.168.10.128:9000/project/upload-images/f3349cded08d6686a93d0a81b9a64ec1e50d9a82cbb88541b37027f085813a15.jpg)  \n儎⑟ഴḽ䆜઀ᛞ࠽व䀜᪮儎⑟Ⲻ䇴༽䜞ԬȾ",
            "score": 0.7386936545372009,
            "source": "local"
        },
        {
            "id": 468279113385596517,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "content": "## 设备\n\n•\t将本设备放置在平整、水平且稳定的表面上（如桌面），避免震动和冲击。\n\n•\t将本设备放置在通风良好的环境中。\n\n•\t为了防止人员受伤，请谨慎操作，避免将手指放置在图中所示的区域中。\n\n![禁止将手指伸入设备内部齿轮区域，以防受伤，需谨慎操作。](http://192.168.10.128:9000/project/upload-images/c61a7f4e923881679f747508ae309c39dc221685344b068009256b1b3a40cc00.jpg)",
            "score": 0.7297913432121277,
            "source": "local"
        },
        {
            "id": 468279113385596518,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "content": "## 设备\n\n![禁止将手指放入设备指定区域，确保操作安全，防止意外伤害。](http://192.168.10.128:9000/project/upload-images/5067b2891ca4f761e2874921e0eb433aa742afbf38ca8dc509afecbf0aa6a6b5.jpg)",
            "score": 0.7218196988105774,
            "source": "local"
        },
        {
            "id": 468279113385596513,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 设备",
            "item_name": "BrotherHAK180烫金机",
            "content": "## 设备\n\n![手正操作打开的设备内部结构，展示其组件布局与安装方式。](http://192.168.10.128:9000/project/upload-images/501bb8d2d681e4502d87badb15a68939eadfa086d309c3599f1c36b0bc559177.jpg)",
            "score": 0.7154430747032166,
            "source": "local"
        },
        {
            "id": 468279113385596522,
            "file_title": "hak180产品安全手册_new",
            "section_title": "## 为设备选择一个安全的位置",
            "item_name": "BrotherHAK180烫金机",
            "content": "## 为设备选择一个安全的位置\n\n![正确搬运设备：双手抓两侧，勿提托板盒，防跌落伤人，确保平稳安全。](http://192.168.10.128:9000/project/upload-images/cc5ee1ac24ebb2707d40dc7a234a8b243f55f5bf08fabc683859be6fdf096ffa.jpg)  \n确保本设备的任何部位均未伸出设备所在的桌面或支架。特别是当本设备位于桌面、支架等边缘时，请勿让出纸盒打开。确保本设备位于平整、水平且稳定的表面上，避免震动。不遵守这些预防措施可能导致设备跌落，从而导致用户的人身伤害以及设备严重损坏。",
            "score": 0.7154254913330078,
            "source": "local"
        },
        {
            "id": 468279113385596502,
            "file_title": "hak180产品安全手册_new",
            "section_title": "无标题",
            "item_name": "BrotherHAK180烫金机",
            "content": "无标题\n\n![条形码图片，下方标注“D01WD7001-00 SCHN HAK 180 烫金机产品安全手册”。](http://192.168.10.128:9000/project/upload-images/677a08ee041965bbbdb6b483d6c17d5aaa36a26b6dc96870a2019f0307b8616f.jpg)  \nD01WD7001-00\n\nSCHN",
            "score": 0.7140102386474609,
            "source": "local"
        },
        {
            "id": 468279113385596501,
            "file_title": "hak180产品安全手册_new",
            "section_title": "无标题",
            "item_name": "BrotherHAK180烫金机",
            "content": "![条形码图片，下方标注“D01WD7001-00 SCHN HAK 180 烫金机产品安全手册”。](http://192.168.10.128:9000/project/upload-images/677a08ee041965bbbdb6b483d6c17d5aaa36a26b6dc96870a2019f0307b8616f.jpg)  \nD01WD7001-00\n\nSCHN\n",
            "score": 0.7139994502067566,
            "source": "local"
        }
    ]
    }
    node_rrf = NodeRrf()
    result = node_rrf(mock_state)
    logger.info(json_format(result))