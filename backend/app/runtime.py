"""Общие объекты процесса: фабрика сессий и отправители сообщений по каналам.

Заполняется при старте приложения (app.main.lifespan); в тестах — подменяется.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.ui import Messenger
from app.models import Channel


@dataclass
class Runtime:
    session_factory: async_sessionmaker[AsyncSession] | None = None
    messengers: dict[Channel, Messenger] = field(default_factory=dict)
    bot_usernames: dict[Channel, str] = field(default_factory=dict)

    def reset(self) -> None:
        self.session_factory = None
        self.messengers.clear()
        self.bot_usernames.clear()


runtime = Runtime()
