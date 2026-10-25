"""
【全新代码】预处理流水线 - 电商小二和掌柜智库都没有

功能：定时拉飞书待办 → 下载合同附件 → 解析合同 → 存库
触发：scheduler.py 每 60 秒自动调用 pull_once()
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from contract_review_service.clients.feishu import get_feishu_client
from contract_review_service.clients.office import convert_word_to_pdf, is_word_file
from contract_review_service.clients.storage import get_storage
from contract_review_service.common.config import get_settings
from contract_review_service.models import (
    ApprovalAttachment,
    ApprovalTask,
    ContractParse,
    ReviewResult,
)
from contract_review_service.services import state_machine
from contract_review_service.services.logger import timed_log
from contract_review_service.services.parser import extract_text, parse_contract
from contract_review_service.services.review import run_review

logger = logging.getLogger(__name__)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fingerprint(attachment: dict[str, Any], client: Any) -> str:
    token = str(attachment.get("file_token") or "").strip()
    if token:
        return token
    return _sha256(client.download_attachment(attachment))


def _attachments_changed(
    db: Session,
    task: ApprovalTask,
    attachments: list[dict[str, Any]],
    client: Any,
) -> bool:
    existing = db.scalars(
        select(ApprovalAttachment).where(
            ApprovalAttachment.task_id == task.id,
            ApprovalAttachment.attachment_version == task.attachment_version,
        )
    ).all()
    if not existing:
        return True
    existing_fp = {row.file_token or row.file_hash for row in existing}
    new_fp = {_fingerprint(item, client) for item in attachments}
    return existing_fp != new_fp


def _save_attachments(
    db: Session,
    task: ApprovalTask,
    attachments: list[dict[str, Any]],
    client: Any,
) -> list[tuple[ApprovalAttachment, Path]]:
    storage = get_storage()
    saved: list[tuple[ApprovalAttachment, Path]] = []
    for index, item in enumerate(attachments):
        content = client.download_attachment(item)
        file_name = str(item.get("file_name") or f"attachment_{index}")
        relative = (
            f"attachments/{task.id}/v{task.attachment_version}/{index}_{file_name}"
        )
        stored = storage.save(relative, content)
        path = Path(stored.local_path)
        record = ApprovalAttachment(
            task_id=task.id,
            attachment_version=task.attachment_version,
            file_name=file_name,
            file_type=str(item.get("file_type") or path.suffix.lstrip(".") or "pdf"),
            file_token=str(item.get("file_token") or ""),
            file_hash=_sha256(content),
            file_size=len(content),
            sort_order=index,
            minio_path=stored.uri,
            converted_file_path=stored.uri,
            download_status="success",
        )
        db.add(record)
        saved.append((record, path))
    db.commit()
    return saved


def _preprocess(db: Session, task: ApprovalTask, approval: dict[str, Any], client: Any) -> dict[str, Any]:
    if task.task_status != "pending":
        return {"success": False, "code": "NOT_PENDING", "message": task.task_status}
    if not state_machine.transition(db, task, "pending", "parsing"):
        return {"success": False, "code": "TASK_BUSY", "message": "任务已被占用"}

    try:
        # 重跑时清掉当前版本的附件记录，避免重复。
        db.query(ApprovalAttachment).filter(
            ApprovalAttachment.task_id == task.id,
            ApprovalAttachment.attachment_version == task.attachment_version,
        ).delete()
        db.commit()

        attachments = approval.get("attachments", [])
        with timed_log(
            db,
            task_id=task.id,
            skill_code="APPROVAL_FETCH",
            tool_name="download_attachment",
            input_json={"count": len(attachments)},
        ) as state:
            saved = _save_attachments(db, task, attachments, client)
            state["output_json"] = {"saved": len(saved)}

        with timed_log(
            db,
            task_id=task.id,
            skill_code="CONTRACT_PARSE",
            tool_name="parse_contract",
            input_json={"files": [row.file_name for row, _ in saved]},
        ) as state:
            texts: list[str] = []
            parse_paths: list[Path] = []
            scanned = False
            for record, path in saved:
                parse_path = path
                if is_word_file(path):
                    # Word → PDF → MinerU；转换后的 PDF 落盘并记录到附件表。
                    converted_dir = (
                        Path(get_settings().storage_dir)
                        / "converted"
                        / str(task.id)
                        / f"v{task.attachment_version}"
                    )
                    parse_path = convert_word_to_pdf(path, converted_dir)
                    stored_pdf = get_storage().save(
                        f"converted/{task.id}/v{task.attachment_version}/{parse_path.name}",
                        parse_path.read_bytes(),
                    )
                    record.converted_file_path = stored_pdf.uri
                    db.commit()
                parse_paths.append(parse_path)

                text, is_scanned = extract_text(parse_path)
                texts.append(text)
                scanned = scanned or is_scanned
            full_text = "\n\n".join(texts)
            if not full_text.strip():
                raise ValueError("解析内容为空")
            parsed = parse_contract(full_text, saved[0][0].file_name if saved else "")
            state["output_json"] = {
                "chars": len(full_text),
                "clauses": len(parsed["clauses"]),
                "is_scanned": scanned,
            }

        parse_record = ContractParse(
            task_id=task.id,
            attachment_version=task.attachment_version,
            merged_pdf_path=parse_paths[0].as_posix() if parse_paths else "",
            full_text=full_text,
            basic_info_json={
                **parsed["basic_info"],
                "normalized": parsed["normalized"],
            },
            clause_info_json={"clauses": parsed["clauses"]},
            chunks_json=parsed["chunks"],
            parse_status="success",
        )
        db.add(parse_record)
        task.is_scanned = scanned
        db.commit()

        if not state_machine.transition(db, task, "parsing", "ready"):
            return {"success": False, "code": "STATE_CONFLICT", "message": "状态推进失败"}

        if task.review_requested:
            return run_review(db, task)
        return {"success": True, "code": "OK", "message": "预处理完成"}

    except Exception as exc:
        logger.exception("预处理失败")
        state_machine.mark_blocked(db, task, stage="preprocess", error=str(exc))
        return {"success": False, "code": "PREPROCESS_FAILED", "message": str(exc)}


def pull_once(db: Session) -> dict[str, Any]:
    """拉一次待办：新建任务、检测附件更新、执行预处理。"""
    client = get_feishu_client()
    approvals = client.list_pending_approvals()
    created = 0
    updated = 0
    processed = 0

    for approval in approvals:
        code = str(approval.get("approval_code") or "").strip()
        if not code:
            continue
        task = db.scalar(select(ApprovalTask).where(ApprovalTask.approval_code == code))
        attachments = approval.get("attachments", [])

        if task is None:
            task = ApprovalTask(
                approval_code=code,
                approval_title=str(approval.get("approval_title") or ""),
                applicant_name=str(approval.get("applicant_name") or ""),
                approval_status=str(approval.get("approval_status") or "pending"),
                task_status="pending",
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            created += 1
        else:
            # parsing 交给当前处理流程；blocked 等人工重试，不自动重跑。
            if task.task_status in {"parsing", "blocked"}:
                continue
            if _attachments_changed(db, task, attachments, client):
                was_done = task.task_status == "done"
                task.attachment_version += 1
                task.task_status = "pending"
                task.failed_stage = None
                task.write_status = "not_written"
                task.retry_count = 0
                # 已审过的合同附件更新后自动按新版本重审。
                task.review_requested = task.review_requested or was_done
                db.execute(
                    update(ContractParse)
                    .where(ContractParse.task_id == task.id)
                    .values(is_superseded=True)
                )
                db.commit()
                updated += 1
            elif task.task_status != "pending":
                continue

        if task.task_status == "pending":
            result = _preprocess(db, task, approval, client)
            if result.get("success"):
                processed += 1

    return {
        "success": True,
        "code": "OK",
        "message": f"拉取完成：新建 {created}，更新 {updated}，预处理 {processed}",
        "data": {"created": created, "updated": updated, "processed": processed},
    }


def trigger_review(db: Session, approval_code: str, force: bool = False) -> dict[str, Any]:
    task = db.scalar(select(ApprovalTask).where(ApprovalTask.approval_code == approval_code))
    if task is None:
        return {"success": False, "code": "TASK_NOT_FOUND", "message": "审批单不存在"}

    if task.task_status == "done" and not force:
        result = db.scalar(
            select(ReviewResult).where(
                ReviewResult.task_id == task.id,
                ReviewResult.attachment_version == task.attachment_version,
            )
        )
        return {
            "success": True,
            "code": "ALREADY_DONE",
            "message": "已审查，返回已有结果",
            "data": {"review_result_id": result.id if result else None},
        }

    if task.task_status in {"pending", "parsing"}:
        state_machine.request_review(db, task)
        return {
            "success": True,
            "code": "QUEUED",
            "message": "预处理未完成，已记录审查请求，完成后自动审查",
        }

    if task.task_status == "blocked":
        return {
            "success": False,
            "code": "TASK_BLOCKED",
            "message": f"任务阻塞于 {task.failed_stage}，请先重试",
        }

    if task.task_status == "reviewing":
        return {"success": False, "code": "TASK_BUSY", "message": "任务正在审查中"}

    if force and task.task_status == "done":
        task.task_status = "ready"
        task.review_requested = True
        db.commit()

    return run_review(db, task)


def retry_task(db: Session, task: ApprovalTask) -> dict[str, Any]:
    if task.task_status != "blocked":
        return {"success": False, "code": "NOT_BLOCKED", "message": "只有 blocked 任务可以重试"}
    state_machine.reset_for_retry(db, task)
    stage = task.failed_stage

    if stage == "preprocess":
        task.task_status = "pending"
        task.failed_stage = None
        db.commit()
        return pull_once(db)

    # 审查或回写失败：回到 ready 重新跑审查，comment_logs 会防重复回写。
    task.task_status = "ready"
    task.failed_stage = None
    db.commit()
    return run_review(db, task)
