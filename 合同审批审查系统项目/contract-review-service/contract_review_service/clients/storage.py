from __future__ import annotations

import email.utils
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Protocol

import httpx

from contract_review_service.common.config import get_settings

logger = logging.getLogger(__name__)
_clock_lock = threading.Lock()
_clock_offset_seconds: float = 0.0


@dataclass(frozen=True)
class StoredObject:
    """保存结果：local_path 供解析使用，uri 用于入库/展示。"""

    local_path: str
    uri: str


class StorageClient(Protocol):
    def save(self, relative_path: str, content: bytes) -> StoredObject: ...


class LocalStorage:
    """本地文件存储，路径相对于 storage_dir。"""

    def __init__(self) -> None:
        self.root = Path(get_settings().storage_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, relative_path: str, content: bytes) -> StoredObject:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredObject(local_path=str(path), uri=str(path))


def _detect_clock_offset(endpoint: str, secure: bool) -> float:
    """读取 MinIO 的 Date 头，计算本机与服务器的时钟差。"""
    scheme = "https" if secure else "http"
    try:
        response = httpx.head(
            f"{scheme}://{endpoint}/minio/health/live",
            timeout=5,
        )
        response.raise_for_status()
        server_date = email.utils.parsedate_to_datetime(response.headers["date"])
        if server_date.tzinfo is None:
            server_date = server_date.replace(tzinfo=UTC)
        offset = (server_date - datetime.now(UTC)).total_seconds()
        logger.info("MinIO 时钟偏移 %.0f 秒，已自动校正", offset)
        return offset
    except Exception:
        logger.warning("MinIO 时钟偏移检测失败，按本机时间请求", exc_info=True)
        return 0.0


def _apply_clock_offset(offset: float) -> None:
    """给 minio SDK 的签名时间加偏移；只打一次补丁，后续更新全局偏移。"""
    if not offset:
        return
    global _clock_offset_seconds
    with _clock_lock:
        import minio.time as minio_time  # type: ignore

        _clock_offset_seconds = offset
        if getattr(minio_time, "_contract_offset_patched", False):
            return
        original_utcnow = minio_time.utcnow

        def patched_utcnow() -> datetime:
            return original_utcnow() + timedelta(seconds=_clock_offset_seconds)

        minio_time.utcnow = patched_utcnow
        minio_time._contract_offset_patched = True  # type: ignore[attr-defined]


class MinioStorage:
    """MinIO 存储：上传对象，同时在本地保留一份工作副本供 MinerU 解析。"""

    def __init__(self) -> None:
        try:
            from minio import Minio  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("使用 MinIO 需要安装 minio 包：pip install minio") from exc

        settings = get_settings()
        endpoint = settings.minio_endpoint.strip()
        secure = settings.minio_secure or endpoint.startswith("https://")
        endpoint = endpoint.removeprefix("http://").removeprefix("https://").rstrip("/")
        if not endpoint:
            raise RuntimeError("CONTRACT_MINIO_ENDPOINT 未配置")

        # 服务器与客户端时钟偏差超过 15 分钟时，S3 签名会被拒绝。
        offset = settings.minio_clock_offset_seconds
        if not offset and settings.minio_auto_clock_sync:
            offset = _detect_clock_offset(endpoint, secure)
        _apply_clock_offset(offset)

        self.bucket = settings.minio_bucket
        self.client = Minio(
            endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=secure,
        )
        self.local_root = Path(settings.storage_dir) / "working"
        self.local_root.mkdir(parents=True, exist_ok=True)

        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def save(self, relative_path: str, content: bytes) -> StoredObject:
        # 1. 上传到 MinIO
        self.client.put_object(
            self.bucket,
            relative_path,
            BytesIO(content),
            length=len(content),
        )
        # 2. 本地保留工作副本，供 MinerU/Office 读取
        local_path = self.local_root / relative_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        return StoredObject(
            local_path=str(local_path),
            uri=f"minio://{self.bucket}/{relative_path}",
        )


def get_storage() -> StorageClient:
    if get_settings().storage_mode.lower() == "minio":
        return MinioStorage()
    return LocalStorage()
