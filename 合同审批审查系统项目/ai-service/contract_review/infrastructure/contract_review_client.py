from typing import Any

import httpx

from contract_review.common.config import get_settings


class ContractReviewClient:
    """访问 8001 Contract Review Service 的内部接口。"""

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token
        self.settings = get_settings()

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Internal-Token": self.settings.contract_review_internal_token,
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _url(self, path: str) -> str:
        return (
            f"{self.settings.contract_review_base_url.rstrip('/')}/"
            f"{path.lstrip('/')}"
        )

    async def get_json(
        self,
        path: str,
        query_params: dict[str, Any] | None = None,
    ) -> Any:
        async with httpx.AsyncClient(
            timeout=self.settings.contract_review_timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.get(
                self._url(path),
                headers=self._headers(),
                params=query_params,
            )
            response.raise_for_status()
            return response.json()

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        async with httpx.AsyncClient(
            timeout=self.settings.contract_review_timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.post(
                self._url(path),
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()
