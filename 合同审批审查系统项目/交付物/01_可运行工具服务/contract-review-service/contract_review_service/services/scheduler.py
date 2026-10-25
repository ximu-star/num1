"""
【全新代码】定时任务 - 电商小二和掌柜智库都没有

功能：每 60 秒自动拉一次飞书待办

为什么不用 APScheduler：
- MVP 阶段少一个依赖，asyncio loop 够用了
- 后面要加 cron 表达式再换

核心函数：
- run_once_sync()：拉一次待办（同步）
- scheduler_loop()：定时循环（异步）
"""
from __future__ import annotations

import asyncio
import logging

from contract_review_service.database import SessionLocal
from contract_review_service.services.pipeline import pull_once

logger = logging.getLogger(__name__)


def run_once_sync() -> dict:
    with SessionLocal() as db:
        return pull_once(db)


async def scheduler_loop(stop_event: asyncio.Event, interval_seconds: int = 60) -> None:
    """每 interval_seconds 拉一次待办；用 asyncio 替代 APScheduler，少一个依赖。"""
    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(run_once_sync)
            logger.info("定时拉取: %s", result.get("message"))
        except Exception:
            logger.exception("定时拉取失败")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
