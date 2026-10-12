"""Тонкий клиент MAX Bot API (https://dev.max.ru/docs-api).

Токен передаётся в заголовке Authorization. Хост platform-api2.max.ru использует сертификат
Russian Trusted Root CA (Минцифры): его нужно установить в системное хранилище сервера
или указать файлом в MAX_CA_FILE.
"""
from __future__ import annotations

import ssl
from typing import Any

import httpx


class MaxApiError(RuntimeError):
    pass


def make_ssl_context(ca_file: str | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context()  # системное хранилище ОС
    if ca_file:
        ctx.load_verify_locations(ca_file)
    return ctx


class MaxClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://platform-api2.max.ru",
        ca_file: str | None = None,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": token},
            timeout=timeout,
            verify=make_ssl_context(ca_file) if transport is None else True,
            transport=transport,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json: Any = None
    ) -> Any:
        r = await self._http.request(method, path, params=params, json=json)
        if r.status_code >= 400:
            raise MaxApiError(f"{method} {path}: HTTP {r.status_code}: {r.text[:300]}")
        data = r.json() if r.content else {}
        if isinstance(data, dict) and data.get("success") is False:
            raise MaxApiError(f"{method} {path}: {data.get('message') or data}")
        return data

    async def get_me(self) -> dict:
        return await self._request("GET", "/me")

    async def send_message(
        self,
        user_id: int | str,
        text: str,
        attachments: list[dict] | None = None,
        fmt: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"text": text[:4000]}
        if attachments:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request("POST", "/messages", params={"user_id": user_id}, json=body)

    async def answer_callback(self, callback_id: str, notification: str) -> dict:
        return await self._request(
            "POST", "/answers", params={"callback_id": callback_id},
            json={"notification": notification},
        )

    async def subscribe(self, url: str, update_types: list[str], secret: str) -> dict:
        return await self._request(
            "POST", "/subscriptions",
            json={"url": url, "update_types": update_types, "secret": secret},
        )

    async def subscriptions(self) -> dict:
        return await self._request("GET", "/subscriptions")

    async def unsubscribe(self, url: str) -> dict:
        return await self._request("DELETE", "/subscriptions", params={"url": url})
