"""Тонкий клиент Telegram Bot API (https://core.telegram.org/bots/api).

Адрес API настраивается (TELEGRAM_API_BASE) — на случай, если api.telegram.org
недоступен с сервера и нужен свой прокси-адрес.
"""
from __future__ import annotations

from typing import Any

import httpx


class TelegramApiError(RuntimeError):
    def __init__(self, message: str, error_code: int | None = None):
        super().__init__(message)
        self.error_code = error_code


class TelegramClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.telegram.org",
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/bot{token}/", timeout=timeout, transport=transport,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        body = {k: v for k, v in params.items() if v is not None}
        try:
            r = await self._http.post(method, json=body)
        except httpx.HTTPError as e:  # в тексте исключения нет токена — только метод
            raise TelegramApiError(f"{method}: {type(e).__name__}") from None
        try:
            data = r.json()
        except ValueError:
            raise TelegramApiError(f"{method}: HTTP {r.status_code}", r.status_code) from None
        if not data.get("ok"):
            raise TelegramApiError(
                f"{method}: {data.get('description') or r.status_code}", data.get("error_code")
            )
        return data.get("result")

    async def get_me(self) -> dict:
        return await self.call("getMe")

    async def send_message(self, chat_id: int | str, text: str, reply_markup: dict | None = None) -> dict:
        return await self.call(
            "sendMessage", chat_id=chat_id, text=text[:4096], reply_markup=reply_markup,
            link_preview_options={"is_disabled": True},
        )

    async def answer_callback(self, callback_id: str, text: str = "") -> bool:
        return await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200] or None)

    async def set_webhook(self, url: str, secret: str, allowed_updates: list[str]) -> bool:
        return await self.call(
            "setWebhook", url=url, secret_token=secret, allowed_updates=allowed_updates,
            drop_pending_updates=True,
        )

    async def webhook_info(self) -> dict:
        return await self.call("getWebhookInfo")

    async def delete_webhook(self) -> bool:
        return await self.call("deleteWebhook")

    async def set_my_commands(self, commands: list[dict]) -> bool:
        return await self.call("setMyCommands", commands=commands)
