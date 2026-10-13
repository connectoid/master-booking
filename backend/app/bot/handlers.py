"""Логика бота: регистрация мастера, услуги, график, записи; действия клиента.

Не зависит от мессенджера: получает Event и отвечает через Messenger.
Payload кнопок: «m:…» — действия мастера, «c:…» — действия клиента.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.parsing import (
    ParseError,
    format_overrides,
    format_period,
    format_schedule,
    group_overrides,
    parse_date_range,
    parse_exceptions,
    parse_schedule,
    parse_services,
    week_overrides,
)
from app.bot.state import clear_state, get_state, set_state
from app.bot.ui import Button, Event, Keyboard, Messenger
from app.channels.identity import ChannelUser
from app.channels.max.adapter import mini_app_link
from app.models import Channel, Master
from app.notify import appointment_cancelled_by_client
from app.services import booking
from app.services import masters as ms

log = logging.getLogger(__name__)

# состояния диалога
ONB_NAME = "onb_name"
ONB_TZ = "onb_tz"
SVC_ADD = "svc_add"
SCH_EDIT = "sch_edit"
OFF_ADD = "off_add"
WEEK_DATES = "week_dates"
WEEK_SCHED = "week_sched"

MENU: Keyboard = [
    [Button("Записи сегодня", payload="m:today"), Button("На неделю", payload="m:week")],
    [Button("Услуги", payload="m:services"), Button("График", payload="m:schedule")],
    [Button("Выходные и отпуск", payload="m:off"), Button("Настройки", payload="m:settings")],
    [Button("Ссылка для клиентов", payload="m:link")],
]


def tz_keyboard() -> Keyboard:
    return [[Button(title, payload=f"m:tz:{tz}")] for title, tz in texts.TIMEZONES]


class Ctx:
    """Всё, что нужно обработчику одного события."""

    def __init__(
        self,
        event: Event,
        channel: Channel,
        session: AsyncSession,
        messenger: Messenger,
        bot_username: str = "",
    ):
        self.event = event
        self.channel = channel
        self.session = session
        self.messenger = messenger
        self.bot_username = bot_username
        self.user = ChannelUser(channel, event.user_id, event.name)

    async def reply(self, text: str, keyboard: Keyboard | None = None) -> None:
        await self.messenger.send(self.event.user_id, text, keyboard)

    async def toast(self, text: str) -> None:
        """Короткое всплывающее уведомление в ответ на нажатие кнопки."""
        if self.event.callback_id:
            try:
                await self.messenger.answer_callback(self.event.callback_id, text)
            except Exception as e:
                log.debug("answer_callback не удался: %s", e)

    def client_link(self, master: Master) -> str | None:
        return mini_app_link(self.bot_username, master.slug) if self.bot_username else None


async def handle_event(ctx: Ctx) -> None:
    e = ctx.event
    if e.kind == "callback" and e.payload.startswith("c:"):
        await client_callback(ctx)
    elif e.kind == "start" and e.payload:
        await client_start(ctx, e.payload)
    else:
        master = await ms.get_master_by_account(ctx.session, ctx.channel, e.user_id)
        if master is None:
            await onboarding(ctx)
        else:
            await master_flow(ctx, master)
    await ctx.session.commit()


# ---------- клиент ----------

async def client_start(ctx: Ctx, slug: str) -> None:
    master = await ms.get_master_by_slug(ctx.session, slug.lower())
    if master is None:
        await ctx.reply("Мастер по этой ссылке не найден. Проверьте ссылку или спросите мастера.")
        return
    link = ctx.client_link(master)
    kb = [[Button("Выбрать время", url=link)]] if link else None
    await ctx.reply(
        f"Онлайн-запись: {master.name}\n\n"
        "Выберите услугу и свободное время — подтверждение и напоминания придут сюда.",
        kb,
    )


async def client_callback(ctx: Ctx) -> None:
    parts = ctx.event.payload.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return
    action, appointment_id = parts[1], int(parts[2])
    if action == "ok":
        await ctx.toast("Спасибо! Ждём вас")
        await ctx.reply("Спасибо, что подтвердили! Ждём вас.")
    elif action == "cancel":
        try:
            await booking.cancel_appointment_by_client(ctx.session, appointment_id, ctx.user)
        except booking.BookingError as err:
            await ctx.toast(err.message)
            await ctx.reply(err.message)
            return
        await ctx.toast("Запись отменена")
        await ctx.reply("Запись отменена. Если захотите прийти в другое время — запишитесь заново "
                        "по ссылке мастера.")
        await appointment_cancelled_by_client(ctx.session, appointment_id)


# ---------- регистрация мастера ----------

async def onboarding(ctx: Ctx) -> None:
    e = ctx.event
    state, data = await get_state(ctx.session, ctx.channel, e.user_id)

    if e.kind == "callback" and e.payload == "m:register":
        await set_state(ctx.session, ctx.channel, e.user_id, ONB_NAME)
        await ctx.reply(texts.ASK_NAME)
        return

    if state == ONB_NAME and e.kind == "text":
        name = e.text.strip()
        if not 2 <= len(name) <= 60:
            await ctx.reply("Имя должно быть от 2 до 60 символов. " + texts.ASK_NAME)
            return
        await set_state(ctx.session, ctx.channel, e.user_id, ONB_TZ, {"name": name})
        await ctx.reply(texts.ASK_TZ, tz_keyboard())
        return

    if state == ONB_TZ and e.kind == "callback" and e.payload.startswith("m:tz:"):
        tz = e.payload.removeprefix("m:tz:")
        if tz not in texts.TIMEZONE_IDS or "name" not in data:
            await ctx.reply(texts.ASK_TZ, tz_keyboard())
            return
        master = await ms.create_master(ctx.session, ctx.channel, e.user_id, data["name"], tz)
        await clear_state(ctx.session, ctx.channel, e.user_id)
        await set_state(ctx.session, ctx.channel, e.user_id, SVC_ADD)
        await ctx.reply(
            f"Готово, {master.name}! Бесплатный период — {ms.TRIAL_DAYS} дней.\n\n"
            "Осталось два шага: услуги и график. Начнём с услуг.\n\n" + texts.SERVICES_HELP
        )
        return

    if state == ONB_TZ:
        await ctx.reply(texts.ASK_TZ, tz_keyboard())
        return

    await clear_state(ctx.session, ctx.channel, e.user_id)
    await ctx.reply(texts.WELCOME, [[Button("Я мастер — настроить запись", payload="m:register")]])


# ---------- мастер ----------

async def master_flow(ctx: Ctx, master: Master) -> None:
    e = ctx.event
    state, _ = await get_state(ctx.session, ctx.channel, e.user_id)

    if e.kind == "callback":
        await master_callback(ctx, master, e.payload)
        return

    if e.kind == "text" and state == SVC_ADD:
        await master_add_services(ctx, master, e.text)
        return
    if e.kind == "text" and state == SCH_EDIT:
        await master_set_schedule(ctx, master, e.text)
        return
    if e.kind == "text" and state == OFF_ADD:
        await master_add_exceptions(ctx, master, e.text)
        return
    if e.kind == "text" and state == WEEK_DATES:
        await master_week_dates(ctx, master, e.text)
        return
    if e.kind == "text" and state == WEEK_SCHED:
        await master_week_schedule(ctx, master, e.text)
        return

    await clear_state(ctx.session, ctx.channel, e.user_id)
    await show_menu(ctx, master)


async def show_menu(ctx: Ctx, master: Master, prefix: str = "") -> None:
    await ctx.reply((prefix + "\n\n" if prefix else "") + f"{master.name} — что открыть?", MENU)


async def master_callback(ctx: Ctx, master: Master, payload: str) -> None:
    uid = ctx.event.user_id
    if payload in ("m:menu", "m:register"):
        await clear_state(ctx.session, ctx.channel, uid)
        await show_menu(ctx, master)
    elif payload == "m:today":
        await show_appointments(ctx, master, days=1)
    elif payload == "m:week":
        await show_appointments(ctx, master, days=7)
    elif payload == "m:services":
        await show_services(ctx, master)
    elif payload == "m:svc_add":
        await set_state(ctx.session, ctx.channel, uid, SVC_ADD)
        await ctx.reply(texts.SERVICES_HELP)
    elif payload.startswith("m:svc_del:") and payload.removeprefix("m:svc_del:").isdigit():
        removed = await ms.deactivate_service(
            ctx.session, master, int(payload.removeprefix("m:svc_del:"))
        )
        await ctx.toast("Услуга удалена" if removed else "Услуга уже удалена")
        await show_services(ctx, master)
    elif payload == "m:schedule":
        await show_schedule(ctx, master)
    elif payload == "m:sch_edit":
        await set_state(ctx.session, ctx.channel, uid, SCH_EDIT)
        await ctx.reply(texts.SCHEDULE_HELP)
    elif payload == "m:link":
        await show_link(ctx, master)
    elif payload == "m:off":
        await show_exceptions(ctx, master)
    elif payload == "m:off_add":
        await set_state(ctx.session, ctx.channel, uid, OFF_ADD)
        await ctx.reply(texts.OFF_HELP, [[Button("Отмена", payload="m:off")]])
    elif payload == "m:week_add":
        await set_state(ctx.session, ctx.channel, uid, WEEK_DATES)
        await ctx.reply(texts.WEEK_DATES_HELP, [[Button("Отмена", payload="m:off")]])
    elif payload.startswith("m:off_del:"):
        await master_delete_exception(ctx, master, payload.removeprefix("m:off_del:"))
    elif payload == "m:settings":
        await clear_state(ctx.session, ctx.channel, uid)
        await show_settings(ctx, master)
    elif payload.startswith("m:lead:") and payload.removeprefix("m:lead:").isdigit():
        await ms.update_settings(ctx.session, master,
                                 min_lead_min=int(payload.removeprefix("m:lead:")))
        await ctx.toast("Сохранено")
        await show_settings(ctx, master)
    elif payload.startswith("m:horizon:") and payload.removeprefix("m:horizon:").isdigit():
        days = int(payload.removeprefix("m:horizon:"))
        if days in texts.HORIZON_OPTIONS:
            await ms.update_settings(ctx.session, master, horizon_days=days)
            await ctx.toast("Сохранено")
        await show_settings(ctx, master)
    elif payload == "m:oneday":
        await ms.update_settings(ctx.session, master,
                                 one_booking_per_day=not master.one_booking_per_day)
        await ctx.toast("Сохранено")
        await show_settings(ctx, master)
    elif payload == "m:settings_tz":
        await ctx.reply(texts.ASK_TZ, tz_keyboard())
    elif payload.startswith("m:tz:"):
        tz = payload.removeprefix("m:tz:")
        if tz in texts.TIMEZONE_IDS:
            await ms.update_settings(ctx.session, master, timezone=tz)
            await ctx.toast("Часовой пояс сохранён")
        await show_settings(ctx, master)
    else:
        await show_menu(ctx, master)


async def show_services(ctx: Ctx, master: Master) -> None:
    services = await ms.active_services(ctx.session, master.id)
    if not services:
        text = "Услуг пока нет. Добавьте их — без услуг клиенты не смогут записаться."
    else:
        lines = [
            f"{i}. {s.name} — {s.duration_min} мин, {texts.fmt_price(s.price)}"
            for i, s in enumerate(services, 1)
        ]
        text = "Ваши услуги:\n" + "\n".join(lines)
    kb: Keyboard = [[Button("Добавить услуги", payload="m:svc_add")]]
    kb += [[Button(f"Удалить «{s.name[:40]}»", payload=f"m:svc_del:{s.id}")] for s in services]
    kb += [[Button("Меню", payload="m:menu")]]
    await ctx.reply(text, kb)


async def master_add_services(ctx: Ctx, master: Master, text: str) -> None:
    try:
        items = parse_services(text)
    except ParseError as err:
        await ctx.reply(f"{err}\n\nПопробуйте ещё раз или нажмите «Меню».",
                        [[Button("Меню", payload="m:menu")]])
        return
    await ms.add_services(ctx.session, master, items)
    await clear_state(ctx.session, ctx.channel, ctx.event.user_id)
    weekly = await ms.get_weekly_schedule(ctx.session, master.id)
    if not weekly:  # первичная настройка: сразу ведём к графику
        await set_state(ctx.session, ctx.channel, ctx.event.user_id, SCH_EDIT)
        await ctx.reply(f"Добавлено услуг: {len(items)}. Теперь рабочий график.\n\n"
                        + texts.SCHEDULE_HELP)
        return
    await show_services(ctx, master)


async def show_schedule(ctx: Ctx, master: Master) -> None:
    weekly = await ms.get_weekly_schedule(ctx.session, master.id)
    text = ("Ваш график:\n" + format_schedule(weekly)) if weekly else "График ещё не задан."
    await ctx.reply(text, [[Button("Изменить график", payload="m:sch_edit")],
                           [Button("Меню", payload="m:menu")]])


async def master_set_schedule(ctx: Ctx, master: Master, text: str) -> None:
    try:
        weekly = parse_schedule(text)
    except ParseError as err:
        await ctx.reply(f"{err}\n\nПопробуйте ещё раз или нажмите «Меню».",
                        [[Button("Меню", payload="m:menu")]])
        return
    if not weekly:
        await ctx.reply("Получились одни выходные — клиенты не смогут записаться. "
                        "Укажите хотя бы один рабочий день, например: пн-пт 10-19")
        return
    await ms.set_weekly_schedule(ctx.session, master, weekly)
    await clear_state(ctx.session, ctx.channel, ctx.event.user_id)
    await ctx.reply("График сохранён:\n" + format_schedule(weekly))
    await show_link(ctx, master)


def master_today(master: Master) -> dt.date:
    return dt.datetime.now(ZoneInfo(master.timezone)).date()


async def show_exceptions(ctx: Ctx, master: Master, prefix: str = "") -> None:
    await clear_state(ctx.session, ctx.channel, ctx.event.user_id)
    overrides = await ms.upcoming_overrides(ctx.session, master.id, master_today(master))
    if overrides:
        body = "Особые дни (вместо обычного графика):\n" + format_overrides(overrides)
    else:
        body = "Особых дней нет — работаете по обычному графику."
    kb: Keyboard = [
        [Button("Добавить выходные или часы", payload="m:off_add")],
        [Button("График на неделю", payload="m:week_add")],
    ]
    for start, end, _ in group_overrides(overrides)[:10]:
        kb.append([Button(f"Удалить {format_period(start, end)}",
                          payload=f"m:off_del:{start.isoformat()}:{end.isoformat()}")])
    kb.append([Button("Меню", payload="m:menu")])
    await ctx.reply((prefix + "\n\n" if prefix else "") + body, kb)


async def _warn_existing(ctx: Ctx, master: Master, days: list[dt.date]) -> str:
    rows = await ms.appointments_on_days(ctx.session, master, days)
    if not rows:
        return ""
    tz = ZoneInfo(master.timezone)
    lines = [
        f"{texts.fmt_when(a.starts_at, tz)} — {c.name}" + (f", {c.phone}" if c.phone else "")
        for a, c in rows[:15]
    ]
    return ("\n\nНа эти даты уже есть записи — они сохранены, договоритесь с клиентами о "
            "переносе или отмене:\n" + "\n".join(lines))


async def _retry(ctx: Ctx, err: ParseError, back: str = "m:off") -> None:
    await ctx.reply(f"{err}\n\nПопробуйте ещё раз.", [[Button("Отмена", payload=back)]])


async def master_add_exceptions(ctx: Ctx, master: Master, text: str) -> None:
    try:
        overrides = parse_exceptions(text, master_today(master))
    except ParseError as err:
        await _retry(ctx, err)
        return
    await ms.set_overrides(ctx.session, master, overrides)
    warn = await _warn_existing(ctx, master, list(overrides))
    await show_exceptions(ctx, master, prefix="Сохранено:\n" + format_overrides(overrides) + warn)


async def master_week_dates(ctx: Ctx, master: Master, text: str) -> None:
    try:
        start, end = parse_date_range(text, master_today(master))
    except ParseError as err:
        await _retry(ctx, err)
        return
    if (end - start).days > 30:
        await _retry(ctx, ParseError("Для особого графика — не больше месяца за раз"))
        return
    await set_state(ctx.session, ctx.channel, ctx.event.user_id, WEEK_SCHED,
                    {"start": start.isoformat(), "end": end.isoformat()})
    await ctx.reply(f"Период: {format_period(start, end)}.\n\n" + texts.WEEK_SCHEDULE_HELP,
                    [[Button("Отмена", payload="m:off")]])


async def master_week_schedule(ctx: Ctx, master: Master, text: str) -> None:
    _, data = await get_state(ctx.session, ctx.channel, ctx.event.user_id)
    try:
        start = dt.date.fromisoformat(data["start"])
        end = dt.date.fromisoformat(data["end"])
        weekly = parse_schedule(text)
    except (KeyError, ValueError) as err:
        if isinstance(err, ParseError):
            await _retry(ctx, err)
        else:
            await show_exceptions(ctx, master)
        return
    overrides = week_overrides(start, end, weekly)
    await ms.set_overrides(ctx.session, master, overrides)
    warn = await _warn_existing(ctx, master, list(overrides))
    await show_exceptions(
        ctx, master,
        prefix=f"Особый график на {format_period(start, end)} сохранён:\n"
               + format_overrides(overrides) + warn,
    )


async def master_delete_exception(ctx: Ctx, master: Master, raw: str) -> None:
    try:
        start_s, end_s = raw.split(":")
        start, end = dt.date.fromisoformat(start_s), dt.date.fromisoformat(end_s)
    except ValueError:
        await show_exceptions(ctx, master)
        return
    await ms.delete_overrides(ctx.session, master, start, end)
    await ctx.toast("Удалено")
    await show_exceptions(ctx, master,
                          prefix=f"{format_period(start, end)}: снова по обычному графику.")


async def show_settings(ctx: Ctx, master: Master) -> None:
    tz_title = next((t for t, tz in texts.TIMEZONES if tz == master.timezone), master.timezone)
    text = (
        "Настройки записи\n\n"
        f"Запись: {texts.fmt_lead(master.min_lead_min)}\n"
        f"Запись открыта на {master.horizon_days} дн. вперёд\n"
        "Одна запись в день на клиента: "
        f"{'включено' if master.one_booking_per_day else 'выключено'}\n"
        f"Часовой пояс: {tz_title}"
    )
    lead_row = [
        Button(("• " if value == master.min_lead_min else "") + title, payload=f"m:lead:{value}")
        for value, title in texts.LEAD_OPTIONS
    ]
    horizon_row = [
        Button(("• " if d == master.horizon_days else "") + f"{d} дн", payload=f"m:horizon:{d}")
        for d in texts.HORIZON_OPTIONS
    ]
    kb: Keyboard = [
        [Button("Минимум до визита:", payload="m:settings")],
        lead_row[:4], lead_row[4:],
        [Button("Открыть запись вперёд на:", payload="m:settings")],
        horizon_row,
        [Button(("Выключить" if master.one_booking_per_day else "Включить")
                + " «одна запись в день»", payload="m:oneday")],
        [Button("Сменить часовой пояс", payload="m:settings_tz")],
        [Button("Меню", payload="m:menu")],
    ]
    await ctx.reply(text, kb)


async def show_link(ctx: Ctx, master: Master) -> None:
    link = ctx.client_link(master)
    if not link:
        await ctx.reply("Ссылка будет доступна, когда бот получит своё имя. Попробуйте позже.")
        return
    await ctx.reply(
        "Ваша ссылка для записи:\n"
        f"{link}\n\n"
        "Отправляйте её клиентам вместо переписки «есть окошко?», добавьте в профиль соцсетей "
        "и закрепите в чатах. Новые записи будут приходить сюда.",
        [[Button("Меню", payload="m:menu")]],
    )


async def show_appointments(ctx: Ctx, master: Master, days: int) -> None:
    tz = ZoneInfo(master.timezone)
    today = dt.datetime.now(tz).date()
    start = dt.datetime.combine(today, dt.time.min, tzinfo=tz)
    end = start + dt.timedelta(days=days)
    rows = await ms.appointments_between(ctx.session, master.id, start, end)
    title = "Сегодня" if days == 1 else "Ближайшие 7 дней"
    if not rows:
        await ctx.reply(f"{title}: записей нет.", [[Button("Меню", payload="m:menu")]])
        return
    lines, current_day = [f"{title}:"], None
    for appt, client in rows:
        local = appt.starts_at.astimezone(tz)
        if days > 1 and local.date() != current_day:
            current_day = local.date()
            lines.append(f"\n{texts.fmt_day(current_day)}")
        who = f"{client.name}, {client.phone}" if client.phone else client.name
        lines.append(
            f"{local:%H:%M}–{appt.ends_at.astimezone(tz):%H:%M} {appt.service_name} — {who}"
        )
    await ctx.reply("\n".join(lines), [[Button("Меню", payload="m:menu")]])


async def process_event(channel: Channel, event: Event) -> None:
    """Точка входа из вебхука (фоновая задача): своя сессия, ошибки — в лог."""
    from app.runtime import runtime

    messenger = runtime.messengers.get(channel)
    if messenger is None or runtime.session_factory is None:
        log.warning("Нет отправителя для канала %s — событие пропущено", channel)
        return
    async with runtime.session_factory() as session:
        ctx = Ctx(event, channel, session, messenger, runtime.bot_usernames.get(channel, ""))
        try:
            await handle_event(ctx)
        except Exception:
            await session.rollback()
            log.exception("Ошибка обработки события %s", event)
