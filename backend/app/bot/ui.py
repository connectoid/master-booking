"""Общие для всех мессенджеров типы бота: входящее событие, кнопки, отправитель."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Button:
    text: str
    payload: str | None = None  # кнопка-действие (callback)
    url: str | None = None      # кнопка-ссылка


Keyboard = list[list[Button]]


@dataclass(frozen=True)
class Event:
    """Входящее событие от мессенджера, уже без особенностей конкретной платформы."""

    kind: Literal["start", "text", "callback"]
    user_id: str
    name: str = ""
    text: str = ""
    payload: str = ""             # параметр /start или payload нажатой кнопки
    callback_id: str | None = None


class Messenger(Protocol):
    async def send(self, user_id: str, text: str, keyboard: Keyboard | None = None) -> None: ...

    async def answer_callback(self, callback_id: str, notification: str) -> None: ...
