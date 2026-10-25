from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from contract_review_service.clients.feishu import get_feishu_client
from contract_review_service.common.config import get_settings
from contract_review_service.models import ApprovalTask, CommentLog

logger = logging.getLogger(__name__)


def already_written(db: Session, task: ApprovalTask) -> bool:
    """同一 task + 附件版本已成功回写则不再写。"""
    existing = db.scalar(
        select(CommentLog.id).where(
            CommentLog.task_id == task.id,
            CommentLog.attachment_version == task.attachment_version,
            CommentLog.write_status == "success",
        ).limit(1)
    )
    return existing is not None


def write_comment(db: Session, task: ApprovalTask, comment_text: str) -> dict:
    if already_written(db, task):
        logger.info("任务 %s 版本 %s 已回写，跳过", task.approval_code, task.attachment_version)
        return {"success": True, "code": "ALREADY_WRITTEN", "message": "已回写，跳过"}

    limit = get_settings().comment_max_length
    text = comment_text.strip()
    if len(text) > limit:
        text = text[: limit - 20].rstrip() + "\n\n（内容过长，已截断）"

    client = get_feishu_client()
    try:
        response = client.write_comment(task.approval_code, text)
    except Exception as exc:
        logger.exception("飞书评论回写失败")
        db.add(
            CommentLog(
                task_id=task.id,
                attachment_version=task.attachment_version,
                write_status="failed",
                write_response_text=str(exc),
            )
        )
        task.write_status = "failed"
        db.commit()
        raise

    db.add(
        CommentLog(
            task_id=task.id,
            attachment_version=task.attachment_version,
            comment_id=str(response.get("comment_id", "")),
            write_status="success",
            write_response_text=str(response),
        )
    )
    task.write_status = "success"
    db.commit()
    return {"success": True, "code": "OK", "message": "回写成功", "data": response}
