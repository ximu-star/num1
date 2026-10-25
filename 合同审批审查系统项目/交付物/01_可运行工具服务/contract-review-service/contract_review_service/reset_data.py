from contract_review_service import models  # noqa: F401
from contract_review_service.database import Base, SessionLocal, engine, init_db
from contract_review_service.seed import seed_demo_data


def reset_data() -> None:
    """开发期重置：删表重建 + 灌初始规则。"""
    Base.metadata.drop_all(engine)
    init_db()
    with SessionLocal() as db:
        seed_demo_data(db)


if __name__ == "__main__":
    reset_data()
    print("Contract review service data reset completed.")
