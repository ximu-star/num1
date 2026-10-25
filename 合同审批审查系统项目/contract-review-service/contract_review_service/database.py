from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from contract_review_service.common.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    # SQLite 在多线程 FastAPI 下需要关闭线程检查；PostgreSQL 不需要。
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(
    get_settings().database_url,
    **_engine_kwargs(get_settings().database_url),
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_schema() -> None:
    """轻量迁移：create_all 不会给已有表加列，这里补 similarity。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "rule_hits" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("rule_hits")}
    if "similarity" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE rule_hits ADD COLUMN similarity FLOAT")
            )


def init_db() -> None:
    """按模型建表。开发期用 create_all；生产建议换 Alembic。"""
    from contract_review_service import models  # noqa: F401

    Base.metadata.create_all(engine)
    _migrate_schema()
