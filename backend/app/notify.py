"""Уведомления мастеру и клиенту о записях. Отправляет через runtime.messengers.

Функции безопасны для вызова из фоновых задач: ошибки отправки логируются, а не падают.
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import fmt_price, fmt_when
from app.bot.ui import Button, Keyboard
from app.models import Appointment, Channel, Client, ClientContact, Master
from app.runtime import runtime
from app.services.masters import master_accounts

log = logging.getLogger(__name__)


def client_keyboard(appointment_id: int) -> Keyboard:
    return [[
        Button("Приду", payload=f"c:ok:{appointment_id}"),
        Button("Отменить запись", payload=f"c:cancel:{appointment_id}"),
    ]]


async def _load(session: AsyncSession, appointment_id: int):
    appt = await session.get(Appointment, appointment_id)
    if appt is None:
        return None
    client = await session.get(Client, appt.client_id)
    master = await session.get(Master, appt.master_id)
    return appt, client, master


async def _send(channel: str | Channel, user_id: str, text: str, kb: Keyboard | None = None):
    messenger = runtime.messengers.get(Channel(channel))
    if messenger is None:
        return
    try:
        await messenger.send(user_id, text, kb)
    except Exception:
        log.exception("Не удалось отправить сообщение %s:%s", channel, user_id)


async def _notify_master(session: AsyncSession, master: Master, text: str) -> None:
    for acc in await master_accounts(session, master.id):
        await _send(acc.channel, acc.external_user_id, text)


async def _client_contacts(session: AsyncSession, client_id: int) -> list[ClientContact]:
    return list(await session.scalars(
        select(ClientContact).where(ClientContact.client_id == client_id).order_by(ClientContact.id)
    ))


def _client_label(client: Client) -> str:
    return f"{client.name}, {client.phone}" if client.phone else client.name


async def appointment_created(appointment_id: int) -> None:
    """Новая запись через Mini App: мастеру — уведомление, клиенту — подтверждение."""
    if not runtime.messengers or runtime.session_factory is None:
        return
    async with runtime.session_factory() as session:
        loaded = await _load(session, appointment_id)
        if loaded is None:
            return
        appt, client, master = loaded
        tz = ZoneInfo(master.timezone)
        when = fmt_when(appt.starts_at, tz)
        await _notify_master(
            session, master,
            f"Новая запись{' (через сайт)' if appt.source == 'web' else ''}\n"
            f"{when}, {appt.service_name}\nКлиент: {_client_label(client)}"
            + (f"\nКомментарий: {appt.comment}" if appt.comment else ""),
        )
        for contact in await _client_contacts(session, client.id):
            await _send(
                contact.channel, contact.external_user_id,
                f"Вы записаны к мастеру {master.name}\n{when}, {appt.service_name} "
                f"({fmt_price(appt.price)})\n\nНапомним заранее. Если планы изменятся, "
                "отмените запись кнопкой ниже.",
                [[Button("Отменить запись", payload=f"c:cancel:{appt.id}")]],
            )


async def appointment_cancelled_by_client(session: AsyncSession, appointment_id: int) -> None:
    loaded = await _load(session, appointment_id)
    if loaded is None:
        return
    appt, client, master = loaded
    when = fmt_when(appt.starts_at, ZoneInfo(master.timezone))
    await _notify_master(
        session, master,
        f"Клиент отменил запись\n{when}, {appt.service_name}\nКлиент: {_client_label(client)}\n\n"
        "Это время снова свободно для записи.",
    )


async def appointment_cancelled_by_master(session: AsyncSession, appointment_id: int) -> None:
    """Мастер отменил запись — сообщаем клиенту, если он на связи в мессенджере."""
    loaded = await _load(session, appointment_id)
    if loaded is None:
        return
    appt, client, master = loaded
    when = fmt_when(appt.starts_at, ZoneInfo(master.timezone))
    for contact in await _client_contacts(session, client.id):
        await _send(
            contact.channel, contact.external_user_id,
            f"Мастер {master.name} отменил вашу запись\n{when}, {appt.service_name}\n\n"
            "Чтобы выбрать другое время, откройте ссылку мастера ещё раз.",
        )


def reminder_text(kind: str, appt: Appointment, master: Master) -> str:
    when = fmt_when(appt.starts_at, ZoneInfo(master.timezone))
    lead = "Завтра" if kind == "day_before" else "Через 2 часа"
    return (
        f"{lead} у вас запись к мастеру {master.name}\n{when}, {appt.service_name}\n\n"
        "Пожалуйста, подтвердите, что придёте, или отмените запись, если планы изменились."
    )
