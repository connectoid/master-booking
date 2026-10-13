"""Перевод между Telegram Bot API и общими типами бота (app.bot.ui)."""
from __future__ import annotations

import logging
from typing import Any

from app.bot.ui import Event, Keyboard
from app.channels.telegram.client import TelegramClient

log = logging.getLogger(__name__)

ALLOWED_UPDATES = ["message", "callback_query"]


def _user_name(user: dict) -> str:
    full = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)
    return (full or user.get("username") or "").strip()


def _start_payload(text: str) -> str | None:
    """«/start», «/start slug», «/start@bot slug» → payload; не команда старта → None."""
    if not text.startswith("/start"):
        return None
    cmd, _, rest = text.partition(" ")
    if cmd != "/start" and not cmd.startswith("/start@"):
        return None
    return rest.strip()


def parse_update(update: dict[str, Any]) -> Event | None:
    """Update из вебхука → Event. Группы, каналы, боты и прочее → None."""
    try:
        if "message" in update:
            msg = update["message"]
            user = msg.get("from") or {}
            if (msg.get("chat") or {}).get("type") != "private" or user.get("is_bot"):
                return None
            text = (msg.get("text") or "").strip()
            uid = str(user["id"])
            payload = _start_payload(text)
            if payload is not None:
                return Event(kind="start", user_id=uid, name=_user_name(user), payload=payload)
            return Event(kind="text", user_id=uid, name=_user_name(user), text=text)
        if "callback_query" in update:
            cb = update["callback_query"]
            user = cb["from"]
            return Event(
                kind="callback", user_id=str(user["id"]), name=_user_name(user),
                payload=cb.get("data") or "", callback_id=str(cb["id"]),
            )
    except (KeyError, TypeError):
        log.warning("Telegram: не удалось разобрать update: %s", str(update)[:500])
    return None


def keyboard_to_markup(keyboard: Keyboard | None) -> dict | None:
    if not keyboard:
        return None
    rows = []
    for row in keyboard:
        buttons = []
        for b in row:
            if b.web_app:
                buttons.append({"text": b.text, "web_app": {"url": b.web_app}})
            elif b.url:
                buttons.append({"text": b.text, "url": b.url})
            else:
                buttons.append({"text": b.text, "callback_data": (b.payload or "-")[:64]})
        rows.append(buttons)
    return {"inline_keyboard": rows}


class TelegramMessenger:
    """Реализация app.bot.ui.Messenger поверх TelegramClient."""

    # Telegram показывает «часики» на кнопке, пока бот не ответит на нажатие
    needs_callback_ack = True

    def __init__(self, client: TelegramClient):
        self.client = client

    async def send(self, user_id: str, text: str, keyboard: Keyboard | None = None) -> None:
        await self.client.send_message(int(user_id), text, keyboard_to_markup(keyboard))

    async def answer_callback(self, callback_id: str, notification: str) -> None:
        await self.client.answer_callback(callback_id, notification)
