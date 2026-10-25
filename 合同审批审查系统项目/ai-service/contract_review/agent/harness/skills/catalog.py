"""
【来自电商小二，改了业务逻辑】Skill 目录

电商小二原版：4 个电商客服 Skill（查订单/查物流/退货/转人工）
本项目改造：4 个合同审查 Skill

4 个 Skill：
1. APPROVAL_FETCH：拉飞书待办、查详情、下附件
2. CONTRACT_PARSE：读合同解析结果（基本信息/条款/切片/扫描件标记）
3. RULE_REVIEW：跑 PG 确定性规则 + Milvus 语义检索
4. REPORT_WRITEBACK：生成审查报告并回写飞书评论区
"""
from contract_review.agent.harness.skills.definition import SkillCode, SkillDefinition


class SkillCatalog:
    """集中保存 Agent 可以按需加载的合同审查技能。"""

    def __init__(self, definitions: tuple[SkillDefinition, ...]):
        self.definitions = {
            definition.code: definition
            for definition in definitions
        }

    def get_skill(self, skill_code: SkillCode) -> SkillDefinition:
        return self.definitions[skill_code]

    def get_skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self.definitions.values())

    def render_index(self) -> str:
        """渲染供模型选择的精简技能索引。"""
        return "\n".join(
            f"- {skill.code}: {skill.description}"
            for skill in self.definitions.values()
        )


SKILL_CATALOG = SkillCatalog(
    (
        SkillDefinition(
            code=SkillCode.APPROVAL_FETCH,
            description="拉取飞书待审审批单、查详情、下载并确认合同附件",
            guidance=(
                "用户要求拉取待办或查看审批单时，先使用 list_pending_approvals。",
                "查看单个审批单的附件和状态时使用 get_approval_detail。",
                "需要确认附件是否已下载时使用 download_attachment。",
                "附件缺失或下载失败时不得猜测合同内容，应进入异常处理。",
            ),
            tools=("list_pending_approvals", "get_approval_detail", "download_attachment"),
        ),
        SkillDefinition(
            code=SkillCode.CONTRACT_PARSE,
            description="读取合同解析结果，包括基本信息、条款、切片和扫描件标记",
            guidance=(
                "需要审查合同条款时，先使用 parse_contract 读取解析结果。",
                "条款结论必须引用解析结果中的原文和位置，不能自行编造。",
                "扫描件（is_scanned）要在最终结论中提示人工核对金额和日期。",
                "解析结果为空或失败时不得继续生成审查结论。",
            ),
            tools=("parse_contract", "get_approval_detail"),
        ),
        SkillDefinition(
            code=SkillCode.RULE_REVIEW,
            description="执行 PG 确定性规则 + Milvus 语义检索，输出风险点",
            guidance=(
                "先使用 run_pg_rules 执行确定性规则，命中结果必须作为风险结论的依据。",
                "需要手册语义参考时使用 search_manual_semantic；检索不到时不要编造依据。",
                "每条风险点必须能回指 rule_hits 的命中记录，不得生成无依据的风险。",
                "金额、比例、天数、税率等规则结论必须来自确定性计算，不能由模型估算。",
            ),
            tools=("run_pg_rules", "search_manual_semantic", "get_approval_detail"),
        ),
        SkillDefinition(
            code=SkillCode.REPORT_WRITEBACK,
            description="生成审查报告并回写飞书审批评论区",
            guidance=(
                "先使用 save_result 读取已保存的审查结果。",
                "回写内容必须包含：风险等级、命中规则列表、中文摘要、审批关注点。",
                "write_comment 前必须确认评论非空、未重复回写；重复回写由服务端拦截。",
                "扫描件要在评论开头提示人工核对金额和日期。",
                "回写失败时进入 blocked，由人工重试，不得声称已经回写成功。",
            ),
            tools=("save_result", "write_comment", "get_approval_detail"),
        ),
    )
)
