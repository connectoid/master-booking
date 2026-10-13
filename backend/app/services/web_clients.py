"""Клиенты, записавшиеся из браузера: ограничения и привязка мессенджера для напоминаний."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.identity import ChannelUser
from app.models import ACTIVE_STATUSES, Appointment, Channel, Client, ClientContact, Master
from app.services.booking import utcnow

MAX_ACTIVE_WEB_BOOKINGS = 3  # из одного браузера к одному мастеру — от случайных и шуточных записей


async def active_bookings(session: AsyncSession, master: Master, wid: str) -> int:
    return await session.scalar(
        select(func.count()).select_from(Appointment)
        .join(ClientContact, ClientContact.client_id == Appointment.client_id)
        .where(
            Appointment.master_id == master.id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.ends_at > utcnow(),
            ClientContact.channel == Channel.WEB.value,
            ClientContact.external_user_id == wid,
        )
    ) or 0


async def link_messenger(
    session: AsyncSession, wid: str, user: ChannelUser
) -> list[tuple[Master, Client]]:
    """Привязать мессенджер к клиенту из браузера (у всех мастеров, к которым он записывался).

    Если человек уже был клиентом этого мастера в мессенджере — объединяем: записи и
    браузер переходят к существующему клиенту, дубль удаляется.
    Возвращает (мастер, клиент) для каждого мастера.
    """
    web_clients = list(await session.scalars(
        select(Client)
        .join(ClientContact, ClientContact.client_id == Client.id)
        .where(ClientContact.channel == Channel.WEB.value, ClientContact.external_user_id == wid)
        .order_by(Client.id)
    ))
    out = []
    for web_client in web_clients:
        master = await session.get(Master, web_client.master_id)
        existing = await session.scalar(
            select(Client)
            .join(ClientContact, ClientContact.client_id == Client.id)
            .where(
                Client.master_id == master.id,
                ClientContact.channel == user.channel.value,
                ClientContact.external_user_id == user.external_user_id,
            )
        )
        if existing is not None and existing.id != web_client.id:
            await session.execute(
                update(Appointment).where(Appointment.client_id == web_client.id)
                .values(client_id=existing.id))
            has_web = await session.scalar(
                select(func.count()).select_from(ClientContact).where(
                    ClientContact.client_id == existing.id,
                    ClientContact.channel == Channel.WEB.value))
            if has_web:  # у клиента уже есть другой браузер — этот просто забываем
                await session.execute(delete(ClientContact).where(ClientContact.client_id == web_client.id))
            else:
                await session.execute(
                    update(ClientContact).where(ClientContact.client_id == web_client.id)
                    .values(client_id=existing.id))
            if not existing.phone and web_client.phone:
                existing.phone = web_client.phone
            await session.execute(delete(Client).where(Client.id == web_client.id))
            out.append((master, existing))
            continue
        if existing is None:
            # у клиента может быть другой аккаунт этого мессенджера — заменяем (один на канал)
            await session.execute(delete(ClientContact).where(
                ClientContact.client_id == web_client.id,
                ClientContact.channel == user.channel.value))
            session.add(ClientContact(client_id=web_client.id, channel=user.channel.value,
                                      external_user_id=user.external_user_id))
        out.append((master, web_client))
    await session.flush()
    return out


async def upcoming(session: AsyncSession, client: Client, now: dt.datetime | None = None) -> list[Appointment]:
    return list(await session.scalars(
        select(Appointment).where(
            Appointment.client_id == client.id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at > (now or utcnow()),
        ).order_by(Appointment.starts_at)
    ))
