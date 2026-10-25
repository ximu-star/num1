from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Iterator

from sqlalchemy.orm import Session

from contract_review_service.models import TaskLog

logger = logging.getLogger(__name__)


def write_log(
    db: Session,
    *,
    task_id: int | None,
    skill_code: str = "",
    tool_name: str = "",
    input_json: dict[str, Any] | None = None,
    output_json: dict[str, Any] | None = None,
    result_status: str = "success",
    duration_ms: int = 0,
    log_level: str = "info",
    log_content: str = "",
) -> TaskLog:
    """写一条全链路日志。合同正文等敏感内容由调用方先脱敏。"""
    record = TaskLog(
        task_id=task_id,
        skill_code=skill_code,
        tool_name=tool_name,
        input_json=input_json,
        output_json=output_json,
        result_status=result_status,
        duration_ms=duration_ms,
        log_level=log_level,
        log_content=log_content,
    )
    db.add(record)
    db.commit()
    return record


@contextmanager
def timed_log(
    db: Session,
    *,
    task_id: int | None,
    skill_code: str,
    tool_name: str,
    input_json: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """记录一次工具调用耗时；调用方在 with 内填写 output / status。"""
    state: dict[str, Any] = {"output_json": None, "result_status": "success", "log_content": ""}
    started = perf_counter()
    try:
        yield state
    except Exception as exc:
        state["result_status"] = "failed"
        state["log_content"] = str(exc)
        raise
    finally:
        write_log(
            db,
            task_id=task_id,
            skill_code=skill_code,
            tool_name=tool_name,
            input_json=input_json,
            output_json=state.get("output_json"),
            result_status=state["result_status"],
            duration_ms=int((perf_counter() - started) * 1000),
            log_level="info" if state["result_status"] == "success" else "error",
            log_content=state.get("log_content", ""),
        )
