from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from contract_review_service.common.config import get_settings
from contract_review_service.database import SessionLocal, get_db, init_db
from contract_review_service.models import (
    ApprovalAttachment,
    ApprovalTask,
    CommentLog,
    ContractParse,
    ReviewResult,
    ReviewRule,
    RuleHit,
    TaskLog,
)
from contract_review_service.schemas import CommentWriteRequest, ReviewTrigger, RuleUpsert, tool_result
from contract_review_service.seed import seed_demo_data
from contract_review_service.services.pipeline import pull_once, retry_task, trigger_review
from contract_review_service.services.review import run_review
from contract_review_service.services.scheduler import scheduler_loop
from contract_review_service.services.writeback import write_comment

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.seed_on_startup:
        with SessionLocal() as db:
            seed_demo_data(db)

    stop_event = asyncio.Event()
    scheduler_task: asyncio.Task | None = None
    if settings.scheduler_enabled:
        scheduler_task = asyncio.create_task(
            scheduler_loop(stop_event, settings.scheduler_interval_seconds)
        )
    yield
    stop_event.set()
    if scheduler_task:
        await scheduler_task


app = FastAPI(title="Contract Review Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_internal(
    token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    if token != settings.internal_service_token:
        raise HTTPException(status_code=401, detail="Invalid internal service token")


InternalAuth = Annotated[None, Depends(require_internal)]


def _task_or_404(db: Session, approval_code: str) -> ApprovalTask:
    task = db.scalar(select(ApprovalTask).where(ApprovalTask.approval_code == approval_code))
    if task is None:
        raise HTTPException(status_code=404, detail="Approval task not found")
    return task


def _task_payload(task: ApprovalTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "approval_code": task.approval_code,
        "approval_title": task.approval_title,
        "applicant_name": task.applicant_name,
        "approval_status": task.approval_status,
        "task_status": task.task_status,
        "review_requested": task.review_requested,
        "failed_stage": task.failed_stage,
        "retry_count": task.retry_count,
        "is_standard_version": task.is_standard_version,
        "is_scanned": task.is_scanned,
        "attachment_version": task.attachment_version,
        "write_status": task.write_status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _attachment_payload(row: ApprovalAttachment) -> dict[str, Any]:
    return {
        "id": row.id,
        "attachment_version": row.attachment_version,
        "file_name": row.file_name,
        "file_type": row.file_type,
        "file_token": row.file_token,
        "file_hash": row.file_hash,
        "file_size": row.file_size,
        "sort_order": row.sort_order,
        "minio_path": row.minio_path,
        "converted_file_path": row.converted_file_path,
        "download_status": row.download_status,
    }


def _hit_payload(db: Session, row: RuleHit) -> dict[str, Any]:
    rule = db.get(ReviewRule, row.rule_id) if row.rule_id else None
    return {
        "id": row.id,
        "rule_id": row.rule_id,
        "rule_code": rule.rule_code if rule else None,
        "rule_name": rule.rule_name if rule else None,
        "rule_category": rule.rule_category if rule else None,
        "risk_level": rule.risk_level if rule else None,
        "attachment_version": row.attachment_version,
        "hit_source": row.hit_source,
        "similarity": row.similarity,
        "evidence_text": row.evidence_text,
        "evidence_position": row.evidence_position,
        "suggestion_text": row.suggestion_text,
        "hit_status": row.hit_status,
    }


def _result_payload(row: ReviewResult | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "attachment_version": row.attachment_version,
        "rules_version": row.rules_version,
        "overall_risk_level": row.overall_risk_level,
        "summary_text": row.summary_text,
        "focus_points": row.focus_points_json,
        "comment_text": row.comment_text,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _log_payload(row: TaskLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "skill_code": row.skill_code,
        "tool_name": row.tool_name,
        "input_json": row.input_json,
        "output_json": row.output_json,
        "result_status": row.result_status,
        "duration_ms": row.duration_ms,
        "log_level": row.log_level,
        "log_content": row.log_content,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _task_detail(db: Session, task: ApprovalTask) -> dict[str, Any]:
    attachments = db.scalars(
        select(ApprovalAttachment)
        .where(ApprovalAttachment.task_id == task.id)
        .order_by(ApprovalAttachment.attachment_version.desc(), ApprovalAttachment.sort_order)
    ).all()
    parse_record = db.scalar(
        select(ContractParse)
        .where(
            ContractParse.task_id == task.id,
            ContractParse.attachment_version == task.attachment_version,
        )
        .order_by(ContractParse.created_at.desc())
        .limit(1)
    )
    hits = db.scalars(
        select(RuleHit)
        .where(
            RuleHit.task_id == task.id,
            RuleHit.attachment_version == task.attachment_version,
        )
        .order_by(RuleHit.id)
    ).all()
    result = db.scalar(
        select(ReviewResult).where(
            ReviewResult.task_id == task.id,
            ReviewResult.attachment_version == task.attachment_version,
        )
    )
    comments = db.scalars(
        select(CommentLog)
        .where(CommentLog.task_id == task.id)
        .order_by(CommentLog.created_at.desc())
    ).all()
    logs = db.scalars(
        select(TaskLog)
        .where(TaskLog.task_id == task.id)
        .order_by(TaskLog.created_at.desc())
        .limit(200)
    ).all()
    return {
        "task": _task_payload(task),
        "attachments": [_attachment_payload(row) for row in attachments],
        "parse": {
            "attachment_version": parse_record.attachment_version,
            "basic_info": parse_record.basic_info_json,
            "clauses": (parse_record.clause_info_json or {}).get("clauses", []),
            "chunks": parse_record.chunks_json,
            "parse_status": parse_record.parse_status,
            "parse_error": parse_record.parse_error,
        }
        if parse_record
        else None,
        "hits": [_hit_payload(db, row) for row in hits],
        "result": _result_payload(result),
        "comments": [
            {
                "id": row.id,
                "attachment_version": row.attachment_version,
                "comment_id": row.comment_id,
                "write_status": row.write_status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in comments
        ],
        "logs": [_log_payload(row) for row in logs],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "contract-review-service"}


# ---------------------------------------------------------------------------
# 内部接口：供 8002 AIService 的 Tool 调用
# ---------------------------------------------------------------------------


@app.post("/internal/v1/approvals/pull")
def internal_pull(_: InternalAuth, db: Annotated[Session, Depends(get_db)]) -> dict:
    return pull_once(db)


@app.post("/internal/v1/reviews/trigger")
def internal_trigger(
    request: ReviewTrigger,
    _: InternalAuth,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    return trigger_review(db, request.approval_code, request.force)


@app.get("/internal/v1/tasks")
def internal_tasks(
    _: InternalAuth,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, le=200),
) -> dict:
    tasks = db.scalars(
        select(ApprovalTask).order_by(ApprovalTask.updated_at.desc()).limit(limit)
    ).all()
    return tool_result(True, "OK", "查询成功", [_task_payload(task) for task in tasks])


@app.get("/internal/v1/tasks/{approval_code}")
def internal_task_detail(
    approval_code: str,
    _: InternalAuth,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    task = _task_or_404(db, approval_code)
    return tool_result(True, "OK", "查询成功", _task_detail(db, task))


@app.post("/internal/v1/tasks/{approval_code}/retry")
def internal_retry(
    approval_code: str,
    _: InternalAuth,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    task = _task_or_404(db, approval_code)
    return retry_task(db, task)


@app.post("/internal/v1/tasks/{approval_code}/comment")
def internal_comment(
    approval_code: str,
    request: CommentWriteRequest,
    _: InternalAuth,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    task = _task_or_404(db, approval_code)
    return write_comment(db, task, request.comment_text)


# ---------------------------------------------------------------------------
# 管理后台接口（5174）
# ---------------------------------------------------------------------------


@app.post("/api/v1/admin/pull")
def admin_pull(db: Annotated[Session, Depends(get_db)]) -> dict:
    return pull_once(db)


@app.get("/api/v1/admin/tasks")
def admin_tasks(
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    limit: int = Query(default=100, le=500),
) -> dict:
    statement = select(ApprovalTask).order_by(ApprovalTask.updated_at.desc()).limit(limit)
    if status:
        statement = statement.where(ApprovalTask.task_status == status)
    tasks = db.scalars(statement).all()
    return tool_result(True, "OK", "查询成功", [_task_payload(task) for task in tasks])


@app.get("/api/v1/admin/tasks/{task_id}")
def admin_task_detail(
    task_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    task = db.get(ApprovalTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return tool_result(True, "OK", "查询成功", _task_detail(db, task))


@app.post("/api/v1/admin/tasks/{task_id}/retry")
def admin_retry(task_id: int, db: Annotated[Session, Depends(get_db)]) -> dict:
    task = db.get(ApprovalTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return retry_task(db, task)


@app.post("/api/v1/admin/tasks/{task_id}/review")
def admin_review(
    task_id: int,
    db: Annotated[Session, Depends(get_db)],
    force: bool = False,
) -> dict:
    task = db.get(ApprovalTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if force and task.task_status == "done":
        task.task_status = "ready"
        db.commit()
    return run_review(db, task)


@app.patch("/api/v1/admin/tasks/{task_id}/standard-version")
def admin_set_standard(
    task_id: int,
    is_standard: bool,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    task = db.get(ApprovalTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.is_standard_version = is_standard
    db.commit()
    return tool_result(True, "OK", "已更新标准版标记")


@app.get("/api/v1/admin/rules")
def admin_rules(db: Annotated[Session, Depends(get_db)]) -> dict:
    rules = db.scalars(select(ReviewRule).order_by(ReviewRule.id)).all()
    return tool_result(
        True,
        "OK",
        "查询成功",
        [
            {
                "id": rule.id,
                "rule_code": rule.rule_code,
                "rule_name": rule.rule_name,
                "rule_category": rule.rule_category,
                "risk_level": rule.risk_level,
                "rule_status": rule.rule_status,
                "match_mode": rule.match_mode,
                "match_text": rule.match_text,
                "target_field": rule.target_field,
                "operator": rule.operator,
                "threshold_value": float(rule.threshold_value) if rule.threshold_value is not None else None,
                "threshold_unit": rule.threshold_unit,
                "is_semantic": rule.is_semantic,
                "suggestion_text": rule.suggestion_text,
                "source_page": rule.source_page,
                "rule_version": rule.rule_version,
            }
            for rule in rules
        ],
    )


@app.post("/api/v1/admin/rules")
def admin_create_rule(request: RuleUpsert, db: Annotated[Session, Depends(get_db)]) -> dict:
    exists = db.scalar(select(ReviewRule).where(ReviewRule.rule_code == request.rule_code))
    if exists:
        raise HTTPException(status_code=409, detail="Rule code exists")
    rule = ReviewRule(**request.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return tool_result(True, "OK", "规则已创建", {"id": rule.id})


@app.patch("/api/v1/admin/rules/{rule_id}")
def admin_update_rule(
    rule_id: int,
    request: RuleUpsert,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    rule = db.get(ReviewRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    for key, value in request.model_dump().items():
        setattr(rule, key, value)
    rule.rule_version += 1
    db.commit()
    return tool_result(True, "OK", "规则已更新")


@app.get("/api/v1/admin/logs")
def admin_logs(
    db: Annotated[Session, Depends(get_db)],
    task_id: int | None = None,
    limit: int = Query(default=200, le=1000),
) -> dict:
    statement = select(TaskLog).order_by(TaskLog.created_at.desc()).limit(limit)
    if task_id is not None:
        statement = statement.where(TaskLog.task_id == task_id)
    rows = db.scalars(statement).all()
    return tool_result(True, "OK", "查询成功", [_log_payload(row) for row in rows])


@app.get("/api/v1/admin/summary")
def admin_summary(db: Annotated[Session, Depends(get_db)]) -> dict:
    tasks = db.scalars(select(ApprovalTask)).all()
    by_status: dict[str, int] = {}
    for task in tasks:
        by_status[task.task_status] = by_status.get(task.task_status, 0) + 1
    return tool_result(
        True,
        "OK",
        "查询成功",
        {
            "total": len(tasks),
            "by_status": by_status,
            "rules": len(db.scalars(select(ReviewRule)).all()),
        },
    )
