"""Хранение состояния диалога (что бот ждёт от пользователя следующим сообщением)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotState, Channel


async def get_state(
    session: AsyncSession, channel: Channel, user_id: str
) -> tuple[str | None, dict]:
    row = (await session.execute(
        select(BotState.state, BotState.data).where(
            BotState.channel == channel.value, BotState.external_user_id == user_id
        )
    )).first()
    if row is None:
        return None, {}
    return row.state, dict(row.data or {})


async def set_state(
    session: AsyncSession,
    channel: Channel,
    user_id: str,
    state: str | None,
    data: dict | None = None,
) -> None:
    stmt = insert(BotState).values(
        channel=channel.value, external_user_id=user_id, state=state, data=data or {}
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[BotState.channel, BotState.external_user_id],
        set_={"state": state, "data": data or {}, "updated_at": func.now()},
    )
    await session.execute(stmt)


async def clear_state(session: AsyncSession, channel: Channel, user_id: str) -> None:
    await set_state(session, channel, user_id, None, {})
