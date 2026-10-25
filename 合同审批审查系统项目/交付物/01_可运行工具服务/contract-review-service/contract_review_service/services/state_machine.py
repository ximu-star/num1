from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from contract_review_service.models import ApprovalTask


def _now() -> datetime:
    return datetime.now(UTC)


def transition(
    db: Session,
    task: ApprovalTask,
    from_status: str,
    to_status: str,
    **fields: Any,
) -> bool:
    """条件更新状态，防止定时任务、事件、人工重试并发重复执行。"""
    values = {"task_status": to_status, "updated_at": _now(), **fields}
    result = db.execute(
        update(ApprovalTask)
        .where(
            ApprovalTask.id == task.id,
            ApprovalTask.task_status == from_status,
        )
        .values(**values)
    )
    db.commit()
    if result.rowcount == 0:
        db.refresh(task)
        return False
    db.refresh(task)
    return True


def mark_blocked(
    db: Session,
    task: ApprovalTask,
    *,
    stage: str,
    error: str,
) -> bool:
    return transition(
        db,
        task,
        task.task_status,
        "blocked",
        failed_stage=stage,
        write_status="failed" if stage == "writeback" else task.write_status,
        updated_at=_now(),
    )


def request_review(db: Session, task: ApprovalTask) -> None:
    task.review_requested = True
    task.updated_at = _now()
    db.commit()


def reset_for_retry(db: Session, task: ApprovalTask) -> None:
    task.retry_count += 1
    task.updated_at = _now()
    db.commit()
