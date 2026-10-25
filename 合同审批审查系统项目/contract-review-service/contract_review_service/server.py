import uvicorn

from contract_review_service.common.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "contract_review_service.main:app",
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    main()
