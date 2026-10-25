"""
【全新代码】8 张数据表 - 电商小二和掌柜智库都没有

表清单：
1. approval_tasks：审批任务（按 approval_code 去重）
2. approval_attachments：合同附件（按版本存历史）
3. contract_parses：合同解析结果（结构化条款 + 切片）
4. review_rules：规则库（PG 确定性规则 + 语义规则标记）
5. rule_hits：规则命中记录（每条合同命中了哪些规则）
6. review_results：审查结果（总风险等级 + 摘要 + 关注点）
7. comment_logs：飞书评论回写日志（防重复回写）
8. task_logs：全链路运行日志（每个 Tool 调用都记）
"""
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from contract_review_service.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ApprovalTask(Base):
    """审批任务，按 approval_code 去重。"""

    __tablename__ = "approval_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    approval_title: Mapped[str] = mapped_column(String(256), default="")
    applicant_name: Mapped[str] = mapped_column(String(128), default="")
    approval_status: Mapped[str] = mapped_column(String(32), default="pending")
    task_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    review_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    is_standard_version: Mapped[bool] = mapped_column(Boolean, default=False)
    is_scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    attachment_version: Mapped[int] = mapped_column(Integer, default=1)
    write_status: Mapped[str] = mapped_column(String(32), default="not_written")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApprovalAttachment(Base):
    """审批附件。converted_file_path 只放单个附件的 PDF 路径。"""

    __tablename__ = "approval_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("approval_tasks.id"), index=True)
    attachment_version: Mapped[int] = mapped_column(Integer, default=1, index=True)
    file_name: Mapped[str] = mapped_column(String(256))
    file_type: Mapped[str] = mapped_column(String(32), default="pdf")
    file_token: Mapped[str] = mapped_column(String(128), default="")
    file_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    minio_path: Mapped[str] = mapped_column(String(512), default="")
    converted_file_path: Mapped[str] = mapped_column(String(512), default="")
    download_status: Mapped[str] = mapped_column(String(32), default="success")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContractParse(Base):
    """合同解析结果，按附件版本保留历史。"""

    __tablename__ = "contract_parses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("approval_tasks.id"), index=True)
    attachment_version: Mapped[int] = mapped_column(Integer, default=1)
    merged_pdf_path: Mapped[str] = mapped_column(String(512), default="")
    full_text: Mapped[str] = mapped_column(Text, default="")
    basic_info_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    clause_info_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    chunks_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    is_superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_status: Mapped[str] = mapped_column(String(32), default="success")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewRule(Base):
    """PG 确定性规则 + 语义规则标记。"""

    __tablename__ = "review_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    rule_name: Mapped[str] = mapped_column(String(128))
    rule_category: Mapped[str] = mapped_column(String(32), default="business")
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    rule_status: Mapped[str] = mapped_column(String(16), default="enabled", index=True)
    match_mode: Mapped[str] = mapped_column(String(32), default="keyword")
    match_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    threshold_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    target_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator: Mapped[str | None] = mapped_column(String(8), nullable=True)
    threshold_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_semantic: Mapped[bool] = mapped_column(Boolean, default=False)
    semantic_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    applies_scope: Mapped[str] = mapped_column(String(32), default="all")
    rule_version: Mapped[int] = mapped_column(Integer, default=1)
    suggestion_text: Mapped[str] = mapped_column(Text, default="")
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RuleHit(Base):
    """规则命中记录。"""

    __tablename__ = "rule_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("approval_tasks.id"), index=True)
    attachment_version: Mapped[int] = mapped_column(Integer, default=1)
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_rules.id"), nullable=True
    )
    hit_source: Mapped[str] = mapped_column(String(16), default="pg")
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_text: Mapped[str] = mapped_column(Text, default="")
    evidence_position: Mapped[str] = mapped_column(String(128), default="")
    suggestion_text: Mapped[str] = mapped_column(Text, default="")
    hit_status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewResult(Base):
    """审查结果，同一 task + 附件版本只保留一条。"""

    __tablename__ = "review_results"
    __table_args__ = (
        UniqueConstraint("task_id", "attachment_version", name="uq_review_result_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("approval_tasks.id"), index=True)
    attachment_version: Mapped[int] = mapped_column(Integer, default=1)
    rules_version: Mapped[int] = mapped_column(Integer, default=1)
    overall_risk_level: Mapped[str] = mapped_column(String(16), default="low")
    summary_text: Mapped[str] = mapped_column(Text, default="")
    focus_points_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    comment_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommentLog(Base):
    """飞书评论回写日志，用于防重复回写。"""

    __tablename__ = "comment_logs"
    __table_args__ = (
        Index("ix_comment_logs_task_version", "task_id", "attachment_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("approval_tasks.id"), index=True)
    attachment_version: Mapped[int] = mapped_column(Integer, default=1)
    comment_id: Mapped[str] = mapped_column(String(128), default="")
    write_status: Mapped[str] = mapped_column(String(32), default="success")
    write_response_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskLog(Base):
    """全链路运行日志。"""

    __tablename__ = "task_logs"
    __table_args__ = (Index("ix_task_logs_task_created", "task_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_tasks.id"), nullable=True, index=True
    )
    skill_code: Mapped[str] = mapped_column(String(64), default="")
    tool_name: Mapped[str] = mapped_column(String(64), default="")
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_status: Mapped[str] = mapped_column(String(16), default="success")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    log_level: Mapped[str] = mapped_column(String(16), default="info")
    log_content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
