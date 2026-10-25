from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolFailureType(StrEnum):
    """定义工具未返回成功结果的原因类型。"""

    BUSINESS = "BUSINESS"
    SERVICE_CALL = "SERVICE_CALL"
    CONTRACT = "CONTRACT"


class ToolFailureCode(StrEnum):
    """定义工具执行层生成的稳定失败结果码。"""

    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    INVALID_TOOL_RESULT = "INVALID_TOOL_RESULT"


class ToolResult[ResultData](BaseModel):
    """定义业务工具返回给 Agent 的统一结果。"""

    success: bool
    code: str
    message: str
    data: ResultData | None = None
    failure_type: ToolFailureType | None = None


class AttachmentData(BaseModel):
    """审批附件元数据。"""

    id: int
    attachment_version: int = 1
    file_name: str
    file_type: str = ""
    file_token: str = ""
    file_hash: str = ""
    file_size: int = 0
    sort_order: int = 0
    minio_path: str = ""
    download_status: str = "success"


class ApprovalTaskData(BaseModel):
    """审批任务摘要。"""

    id: int
    approval_code: str
    approval_title: str = ""
    applicant_name: str = ""
    approval_status: str = "pending"
    task_status: str = "pending"
    review_requested: bool = False
    failed_stage: str | None = None
    retry_count: int = 0
    is_standard_version: bool = False
    is_scanned: bool = False
    attachment_version: int = 1
    write_status: str = "not_written"


class ClauseData(BaseModel):
    """条款抽取结果。"""

    clause_no: str
    title: str = ""
    category: str = "other"
    text: str = ""
    page: int = 1
    char_start: int = 0
    normalized: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class ParseData(BaseModel):
    """合同解析结果。"""

    attachment_version: int = 1
    basic_info: dict[str, Any] = Field(default_factory=dict)
    clauses: list[ClauseData] = Field(default_factory=list)
    chunks: list[dict[str, Any]] = Field(default_factory=list)
    parse_status: str = "success"
    parse_error: str | None = None


class RuleHitData(BaseModel):
    """规则命中记录。"""

    id: int
    rule_id: int | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    rule_category: str | None = None
    risk_level: str | None = None
    attachment_version: int = 1
    hit_source: str = "pg"
    similarity: float | None = None
    evidence_text: str = ""
    evidence_position: str = ""
    suggestion_text: str = ""
    hit_status: str = "open"


class ReviewResultData(BaseModel):
    """审查结果。"""

    id: int
    attachment_version: int = 1
    rules_version: int = 1
    overall_risk_level: str = "low"
    summary_text: str = ""
    focus_points: list[str] = Field(default_factory=list)
    comment_text: str = ""


class ApprovalDetailData(BaseModel):
    """审批单详情，包含附件、解析、命中和结果。"""

    task: ApprovalTaskData
    attachments: list[AttachmentData] = Field(default_factory=list)
    parse: ParseData | None = None
    hits: list[RuleHitData] = Field(default_factory=list)
    result: ReviewResultData | None = None
    comments: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[dict[str, Any]] = Field(default_factory=list)


class PullResultData(BaseModel):
    """待办拉取结果。"""

    created: int = 0
    updated: int = 0
    processed: int = 0


class TriggerResultData(BaseModel):
    """审查触发结果。"""

    review_result_id: int | None = None
    overall_risk_level: str | None = None
    hit_count: int | None = None
