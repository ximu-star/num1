from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Contract Review Service 运行配置。"""

    # 默认用 SQLite，开箱即可跑；生产可通过 .env 切回 PostgreSQL。
    database_url: str = f"sqlite:///{BASE_DIR / 'contract_review.db'}"

    api_host: str = "127.0.0.1"
    api_port: int = 8001
    internal_service_token: str = "contract-internal-token"

    # 飞书：mock 走 samples/approvals.json，real 走飞书开放平台。
    feishu_mode: str = "mock"
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_base_url: str = "https://open.feishu.cn/open-apis"

    # 存储：默认本地目录，可换 MinIO。
    storage_mode: str = "local"
    storage_dir: str = str(BASE_DIR / "storage")
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "contract-review"
    minio_secure: bool = False
    minio_auto_clock_sync: bool = True
    minio_clock_offset_seconds: float = 0.0

    # 解析：mock 读同目录同名 .md；api 调 MinerU 云端；cli 调本地 MinerU。
    mineru_mode: str = "mock"  # mock / api / cli
    mineru_transport: str = "auto"  # auto / httpx / curl
    mineru_token: str = ""
    mineru_base_url: str = "https://mineru.net/api/v4"
    mineru_model_version: str = "vlm"
    mineru_cli_command: str = "mineru"
    mineru_poll_interval_seconds: int = 3
    mineru_timeout_seconds: int = 300

    # Word → PDF：LibreOffice 路径；留空则自动查找，Windows 本地可回退 Word COM。
    soffice_path: str = ""

    # 语义检索：未配置时自动降级为只跑 PG 规则。
    milvus_enabled: bool = False
    milvus_url: str = ""
    milvus_collection: str = "contract_manual_chunks"
    bge_m3_path: str = ""
    bge_device: str = "cpu"
    bge_dtype: str = "bfloat16"
    bge_batch_size: int = 8
    semantic_top_k: int = 3
    semantic_score_threshold: float = 0.55

    sample_dir: str = str(BASE_DIR / "samples")
    seed_on_startup: bool = True
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 60
    comment_max_length: int = 3000

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_prefix="CONTRACT_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
