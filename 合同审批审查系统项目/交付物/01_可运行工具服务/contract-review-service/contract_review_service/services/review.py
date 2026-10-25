"""
【全新代码】审查逻辑 - 电商小二和掌柜智库都没有

功能：跑规则审查 → 生成审查报告 → 回写飞书评论

核心流程：
1. 查最新的解析结果（basic_info + clauses + chunks）
2. 调 run_rules() 跑 PG 确定性规则
3. 汇总命中的规则，算总风险等级
4. 生成中文摘要（build_summary）
5. 生成审批关注点（build_focus_points）
6. 生成评论内容（build_comment）
7. 存审查结果
8. 调 write_comment() 回写飞书
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from contract_review_service.models import (
    ApprovalTask,
    ContractParse,
    ReviewResult,
    ReviewRule,
    RuleHit,
)
from contract_review_service.services import state_machine
from contract_review_service.services.logger import timed_log
from contract_review_service.services.rules import overall_risk, run_rules
from contract_review_service.services.writeback import write_comment

logger = logging.getLogger(__name__)

RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}
CATEGORY_LABELS = {"business": "商务重点", "legal": "法务重点", "both": "双方共审"}


def _latest_parse(db: Session, task: ApprovalTask) -> ContractParse | None:
    return db.scalar(
        select(ContractParse)
        .where(
            ContractParse.task_id == task.id,
            ContractParse.attachment_version == task.attachment_version,
            ContractParse.is_superseded.is_(False),
        )
        .order_by(ContractParse.created_at.desc())
        .limit(1)
    )


def _rules_version(db: Session) -> int:
    return int(db.scalar(select(func.max(ReviewRule.rule_version))) or 1)


def _build_summary(hits: list[dict[str, Any]], risk: str) -> str:
    if not hits:
        return "本合同未命中现有规则库中的风险规则，建议按常规流程审批。"
    high = sum(1 for hit in hits if hit["risk_level"] == "high")
    medium = sum(1 for hit in hits if hit["risk_level"] == "medium")
    low = len(hits) - high - medium
    top_names = list(dict.fromkeys(hit["rule_name"] for hit in hits[:5]))
    names = "；".join(
        f"{index}. {name}" for index, name in enumerate(top_names, start=1)
    )
    return (
        f"本合同共命中 {len(hits)} 条规则，整体风险等级为{RISK_LABELS.get(risk, risk)}；"
        f"其中高风险 {high} 条、中风险 {medium} 条、低风险 {low} 条。"
        f"主要风险集中在：{names}。建议审批人重点核对命中证据与原文位置，必要时走人工复核。"
    )


def _build_focus_points(hits: list[dict[str, Any]]) -> list[str]:
    if not hits:
        return ["未发现明显风险，建议按常规流程审批。"]
    ordered = sorted(hits, key=lambda item: {"high": 3, "medium": 2, "low": 1}.get(item["risk_level"], 0), reverse=True)
    points: list[str] = []
    for hit in ordered[:5]:
        suggestion = hit.get("suggestion_text") or "建议人工复核该条款。"
        points.append(f"{hit['rule_name']}：{suggestion}")
    return points


def build_comment(
    task: ApprovalTask,
    hits: list[dict[str, Any]],
    risk: str,
    summary: str,
    focus_points: list[str],
) -> str:
    lines: list[str] = []
    if task.is_scanned:
        lines.append("本合同为扫描件识别，关键金额/日期建议人工核对。\n")

    lines.append(f"总风险等级：{RISK_LABELS.get(risk, risk)}\n")
    lines.append("命中规则列表：")
    if not hits:
        lines.append("- 未命中现有规则")
    for hit in hits:
        lines.append(
            f"- 规则名称：{hit['rule_name']}（{CATEGORY_LABELS.get(hit['rule_category'], hit['rule_category'])}）"
        )
        lines.append(f"  风险等级：{RISK_LABELS.get(hit['risk_level'], hit['risk_level'])}")
        lines.append(f"  命中证据：{hit['evidence_text']}")
        lines.append(f"  位置：{hit['evidence_position']}")
        lines.append(f"  建议：{hit['suggestion_text'] or '建议人工复核'}")
        if task.is_scanned and hit["rule_category"] == "business":
            lines.append("  （OCR识别数据，建议人工确认）")

    lines.append("\n中文摘要：")
    lines.append(summary)
    lines.append("\n审批关注点：")
    for index, point in enumerate(focus_points, start=1):
        lines.append(f"{index}. {point}")
    return "\n".join(lines)


def run_review(db: Session, task: ApprovalTask) -> dict[str, Any]:
    """ready → reviewing → done；失败进 blocked 并记录 failed_stage。"""
    if task.task_status == "done":
        result = db.scalar(
            select(ReviewResult).where(
                ReviewResult.task_id == task.id,
                ReviewResult.attachment_version == task.attachment_version,
            )
        )
        return {
            "success": True,
            "code": "ALREADY_DONE",
            "message": "任务已审查，返回已有结果",
            "data": {"review_result_id": result.id if result else None},
        }

    if task.task_status != "ready":
        return {
            "success": False,
            "code": "TASK_NOT_READY",
            "message": f"任务状态为 {task.task_status}，暂不能审查",
        }

    if not state_machine.transition(db, task, "ready", "reviewing"):
        return {"success": False, "code": "TASK_BUSY", "message": "任务已被其他流程占用"}

    try:
        parse_record = _latest_parse(db, task)
        if not parse_record:
            state_machine.mark_blocked(db, task, stage="preprocess", error="缺少解析结果")
            return {"success": False, "code": "NO_PARSE", "message": "缺少解析结果"}

        parse = {
            "full_text": parse_record.full_text,
            "clauses": (parse_record.clause_info_json or {}).get("clauses", []),
            "basic_info": parse_record.basic_info_json or {},
            "normalized": (parse_record.basic_info_json or {}).get("normalized", {}),
        }

        with timed_log(
            db,
            task_id=task.id,
            skill_code="RULE_REVIEW",
            tool_name="run_pg_rules",
            input_json={"approval_code": task.approval_code},
        ) as state:
            hits = run_rules(db, task, parse)
            state["output_json"] = {"hit_count": len(hits)}

        # 重新审查时先清掉当前版本的旧命中，避免重复累积。
        db.execute(
            delete(RuleHit).where(
                RuleHit.task_id == task.id,
                RuleHit.attachment_version == task.attachment_version,
            )
        )
        for hit in hits:
            db.add(
                RuleHit(
                    task_id=task.id,
                    attachment_version=task.attachment_version,
                    rule_id=hit["rule_id"],
                    hit_source=hit["hit_source"],
                    similarity=hit.get("similarity"),
                    evidence_text=hit["evidence_text"],
                    evidence_position=hit["evidence_position"],
                    suggestion_text=hit["suggestion_text"],
                    hit_status=hit["hit_status"],
                )
            )
        db.commit()

        risk = overall_risk(hits)
        summary = _build_summary(hits, risk)
        focus_points = _build_focus_points(hits)
        comment_text = build_comment(task, hits, risk, summary, focus_points)

        result = db.scalar(
            select(ReviewResult).where(
                ReviewResult.task_id == task.id,
                ReviewResult.attachment_version == task.attachment_version,
            )
        )
        if result is None:
            result = ReviewResult(
                task_id=task.id,
                attachment_version=task.attachment_version,
            )
            db.add(result)
        result.rules_version = _rules_version(db)
        result.overall_risk_level = risk
        result.summary_text = summary
        result.focus_points_json = focus_points
        result.comment_text = comment_text
        db.commit()

        try:
            write_comment(db, task, comment_text)
        except Exception as exc:
            logger.exception("审查完成但回写失败")
            state_machine.mark_blocked(db, task, stage="writeback", error=str(exc))
            return {"success": False, "code": "WRITEBACK_FAILED", "message": str(exc)}

        if not state_machine.transition(db, task, "reviewing", "done", failed_stage=None):
            return {"success": False, "code": "STATE_CONFLICT", "message": "状态推进失败"}
        return {
            "success": True,
            "code": "OK",
            "message": "审查完成并已回写",
            "data": {
                "review_result_id": result.id,
                "overall_risk_level": risk,
                "hit_count": len(hits),
            },
        }
    except Exception as exc:
        logger.exception("审查流程失败")
        state_machine.mark_blocked(db, task, stage="review", error=str(exc))
        return {"success": False, "code": "REVIEW_FAILED", "message": str(exc)}
