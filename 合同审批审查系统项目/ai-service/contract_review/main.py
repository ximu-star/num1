import uvicorn

from contract_review.common.config import get_settings
from contract_review.common.event_loop import run_async


async def serve() -> None:
    settings = get_settings()
    server = uvicorn.Server(
        uvicorn.Config(
            "contract_review.app.app:app",
            host=settings.api_host,
            port=settings.api_port,
            loop="none"
        )
    )
    await server.serve()


if __name__ == "__main__":
    run_async(serve())
