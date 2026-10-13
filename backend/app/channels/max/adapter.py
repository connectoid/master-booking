"""Перевод между MAX Bot API и общими типами бота (app.bot.ui)."""
from __future__ import annotations

import logging
from typing import Any

from app.bot.ui import Event, Keyboard
from app.channels.max.client import MaxClient

log = logging.getLogger(__name__)

UPDATE_TYPES = ["bot_started", "message_created", "message_callback"]


def _user_name(user: dict) -> str:
    full = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)
    return (full or user.get("name") or user.get("username") or "").strip()


def parse_update(update: dict[str, Any]) -> Event | None:
    """Update из вебхука → Event. Неинтересные и групповые события → None."""
    try:
        kind = update.get("update_type")
        if kind == "bot_started":
            user = update.get("user") or {}
            return Event(
                kind="start",
                user_id=str(user["user_id"]),
                name=_user_name(user),
                payload=(update.get("payload") or "").strip(),
            )
        if kind == "message_created":
            msg = update.get("message") or {}
            sender = msg.get("sender") or {}
            recipient = msg.get("recipient") or {}
            if sender.get("is_bot"):
                return None
            if recipient.get("chat_type") not in (None, "dialog"):
                return None  # бот работает только в личных диалогах
            text = ((msg.get("body") or {}).get("text") or "").strip()
            if text == "/start" or text.startswith("/start "):
                return Event(
                    kind="start", user_id=str(sender["user_id"]), name=_user_name(sender),
                    payload=text[len("/start"):].strip(),
                )
            return Event(
                kind="text", user_id=str(sender["user_id"]), name=_user_name(sender), text=text
            )
        if kind == "message_callback":
            cb = update.get("callback") or {}
            user = cb.get("user") or {}
            return Event(
                kind="callback",
                user_id=str(user["user_id"]),
                name=_user_name(user),
                payload=cb.get("payload") or "",
                callback_id=cb.get("callback_id"),
            )
    except (KeyError, TypeError):
        log.warning("MAX: не удалось разобрать update: %s", str(update)[:500])
    return None


def keyboard_to_attachments(keyboard: Keyboard | None) -> list[dict] | None:
    if not keyboard:
        return None
    rows = []
    for row in keyboard:
        buttons = []
        for b in row:
            if b.url or b.web_app:
                buttons.append({"type": "link", "text": b.text, "url": b.url or b.web_app})
            else:
                buttons.append({"type": "callback", "text": b.text, "payload": b.payload or ""})
        rows.append(buttons)
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


class MaxMessenger:
    """Реализация app.bot.ui.Messenger поверх MaxClient."""

    def __init__(self, client: MaxClient):
        self.client = client

    async def send(self, user_id: str, text: str, keyboard: Keyboard | None = None) -> None:
        await self.client.send_message(
            int(user_id), text, attachments=keyboard_to_attachments(keyboard)
        )

    async def answer_callback(self, callback_id: str, notification: str) -> None:
        await self.client.answer_callback(callback_id, notification)


def mini_app_link(bot_username: str, slug: str) -> str:
    """Ссылка, открывающая Mini App записи к мастеру (slug придёт в start_param)."""
    return f"https://max.ru/{bot_username}?startapp={slug}"


def bot_start_link(bot_username: str, slug: str) -> str:
    return f"https://max.ru/{bot_username}?start={slug}"


def cabinet_link(bot_username: str) -> str:
    """Mini App в режиме кабинета мастера."""
    return f"https://max.ru/{bot_username}?startapp=cabinet"
