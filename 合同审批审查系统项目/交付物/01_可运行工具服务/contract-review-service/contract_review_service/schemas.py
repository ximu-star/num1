from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


def tool_result(
    success: bool,
    code: str,
    message: str,
    data: Any = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """与电商小二保持一致的统一工具返回结构。"""
    return {
        "success": success,
        "code": code,
        "message": message,
        "data": data,
        "retryable": retryable,
    }


class RuleUpsert(BaseModel):
    rule_code: str = Field(min_length=1, max_length=64)
    rule_name: str = Field(min_length=1, max_length=128)
    rule_category: Literal["business", "legal", "both"] = "business"
    risk_level: Literal["low", "medium", "high"] = "medium"
    rule_status: Literal["enabled", "disabled"] = "enabled"
    match_mode: Literal["keyword", "regex", "threshold", "semantic"] = "keyword"
    match_text: str | None = None
    threshold_value: Decimal | None = None
    target_field: str | None = None
    operator: Literal[">", ">=", "<", "<=", "=", "contains", "regex", "missing"] | None = None
    threshold_unit: str | None = None
    is_semantic: bool = False
    semantic_query: str | None = None
    applies_scope: Literal["all", "standard_only", "nonstandard_only"] = "all"
    rule_version: int = 1
    suggestion_text: str = ""
    source_page: int | None = None


class ReviewTrigger(BaseModel):
    approval_code: str
    force: bool = False


class CommentWriteRequest(BaseModel):
    approval_code: str
    comment_text: str
    attachment_version: int = 1
