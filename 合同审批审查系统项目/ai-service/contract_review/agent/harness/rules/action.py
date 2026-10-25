"""
【来自电商小二，改了业务逻辑】动作校验器

电商小二原版：允许发货/退款/改地址等写操作
本项目改造：只允许 WRITE_COMMENT 一个写操作

4 条规则：
1. 回写前必须确认任务状态是 reviewing
2. 同一 task + attachment_version 不能重复回写
3. 回写内容必须非空，不超过 3000 字
4. 回写失败进 blocked，不能谎称成功
"""
from enum import StrEnum

MAX_COMMENT_LENGTH = 3000


class WriteActionCode(StrEnum):
    """定义 Agent 允许建议的写操作。"""

    WRITE_COMMENT = "WRITE_COMMENT"


WRITE_ACTION_RULES: tuple[str, ...] = (
    "回写评论是写操作，必须先确认任务状态为 reviewing。",
    "同一 task + attachment_version 已经成功回写时不得重复回写。",
    "回写内容必须非空，且不超过飞书评论长度上限。",
    "回写失败时进入 blocked，由人工重试，不得声称已经成功。",
)


def render_write_rules() -> str:
    return "\n".join(f"- {rule}" for rule in WRITE_ACTION_RULES)
