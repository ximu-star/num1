from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    """Contract Review AI Service 运行配置。"""

    ai_database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'ai_service.db'}"
    api_host: str = "0.0.0.0"
    api_port: int = 8002
    internal_service_token: str = "contract-internal-token"
    jwt_secret: str = "replace-this-demo-secret"
    jwt_algorithm: str = "HS256"

    contract_review_base_url: str = "http://127.0.0.1:8001"
    contract_review_internal_token: str = "contract-internal-token"
    contract_review_timeout_seconds: float = 30.0

    llm_provider: Literal["openai_compatible", "deepseek", "qwen"] = "openai_compatible"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_thinking_mode: Literal["disabled", "enabled"] = "disabled"
    llm_timeout_seconds: float = Field(default=30, gt=0)
    llm_max_retries: int = 1

    history_message_limit: int = Field(default=10, ge=1, le=60)
    history_character_budget: int = Field(default=12000, ge=1)
    prompt_version: str = "contract-review-v1"

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
