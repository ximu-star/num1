from langchain.agents.middleware import ModelRequest, dynamic_prompt

from contract_review.agent.harness.rules.action import render_write_rules
from contract_review.agent.harness.skills.catalog import SKILL_CATALOG
from contract_review.agent.harness.skills.definition import SkillCode
from contract_review.agent.llm.prompt import BASE_PROMPT


def render_skill_prompt(active_skill_code: SkillCode | None) -> str:
    """根据当前状态生成 Skill 提示词。"""

    if active_skill_code is None:
        return (
            "【动态 Skill 路由】\n"
            "处理合同审查任务前，先判断当前步骤：拉取审批、解析合同、规则审查或报告回写；"
            "调用 load_skill 加载对应领域。\n"
            f"可用 Skill：\n{SKILL_CATALOG.render_index()}"
        )

    skill = SKILL_CATALOG.get_skill(active_skill_code)
    guidance = "\n".join(f"- {item}" for item in skill.guidance)

    return (
        f"【当前 Skill：{skill.code}】\n"
        f"{guidance}\n"
        "完成当前阶段所需工具调用后，再进入下一个 Skill。"
    )


def render_contract_prompt(active_skill_code: SkillCode | None) -> str:
    skill_prompt = render_skill_prompt(active_skill_code)
    write_rules = f"【写操作规则】\n{render_write_rules()}"
    return f"{BASE_PROMPT}\n\n{skill_prompt}\n\n{write_rules}"


@dynamic_prompt
def contract_prompt(request: ModelRequest) -> str:
    """为每次模型调用生成当前能力范围内的系统提示词。"""
    return render_contract_prompt(request.state.get("active_skill_code"))
