"""
【全新代码】飞书客户端 - 电商小二和掌柜智库都没有

三种实现：
1. MockFeishuClient：本地演示，从 samples/approvals.json 读待办，评论写到本地文件
2. RealFeishuClient：真实飞书 API 占位（上线前实现）

接口：
- list_pending_approvals()：拉待处理审批单
- download_attachment()：下载合同附件
- write_comment()：回写审查意见到评论区
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from contract_review_service.common.config import BASE_DIR, get_settings


class FeishuClient(Protocol):
    """飞书适配接口。mock 与真实实现保持同一契约。"""

    def list_pending_approvals(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def download_attachment(self, attachment: dict[str, Any]) -> bytes: ...

    def write_comment(self, approval_code: str, comment_text: str) -> dict[str, Any]: ...


class MockFeishuClient:
    """本地演示实现：待办来自 samples/approvals.json，评论写到 storage/mock_comments。"""

    def __init__(self) -> None:
        settings = get_settings()
        self.sample_dir = Path(settings.sample_dir)
        self.storage_dir = Path(settings.storage_dir)
        self.comment_dir = self.storage_dir / "mock_comments"
        self.comment_dir.mkdir(parents=True, exist_ok=True)

    def list_pending_approvals(self, limit: int = 50) -> list[dict[str, Any]]:
        path = self.sample_dir / "approvals.json"
        if not path.exists():
            return []
        items = json.loads(path.read_text(encoding="utf-8"))
        return items[:limit]

    def download_attachment(self, attachment: dict[str, Any]) -> bytes:
        raw_path = attachment.get("file_path", "")
        path = Path(raw_path)
        if not path.is_absolute():
            path = BASE_DIR / raw_path
        if not path.exists():
            raise FileNotFoundError(f"附件不存在: {path}")
        return path.read_bytes()

    def write_comment(self, approval_code: str, comment_text: str) -> dict[str, Any]:
        # 每次回写单独成文件，保留多版本评论历史。
        now = datetime.now(UTC)
        stamp = now.strftime("%Y%m%d%H%M%S%f")
        path = self.comment_dir / f"{approval_code}_{stamp}.md"
        path.write_text(
            f"<!-- written_at: {now.isoformat()} -->\n{comment_text}\n",
            encoding="utf-8",
        )
        return {
            "comment_id": f"mock-{approval_code}-{stamp}",
            "path": str(path),
        }


class RealFeishuClient:
    """真实飞书客户端占位。

    设计文档第 9 节已把「评论区能否 @ 机器人 / 事件是否带 approval_code」
    列为阻断项；验证通过后在此实现 token 获取、待办拉取与评论回写。
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise RuntimeError("未配置 CONTRACT_FEISHU_APP_ID / CONTRACT_FEISHU_APP_SECRET")

    def list_pending_approvals(self, limit: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError("真实飞书待办拉取待 Phase 0 验证后实现")

    def download_attachment(self, attachment: dict[str, Any]) -> bytes:
        raise NotImplementedError("真实飞书附件下载待 Phase 0 验证后实现")

    def write_comment(self, approval_code: str, comment_text: str) -> dict[str, Any]:
        raise NotImplementedError("真实飞书评论回写待 Phase 0 验证后实现")


def get_feishu_client() -> FeishuClient:
    mode = get_settings().feishu_mode.lower()
    if mode == "real":
        return RealFeishuClient()
    return MockFeishuClient()
