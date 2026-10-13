"""Ссылки на Mini App и бота для каждого мессенджера.

MAX: обычные ссылки max.ru/<бот>?startapp=… (открывают Mini App).
Telegram: в сообщениях бота — кнопки web_app с адресом нашей Mini App (откроется сразу,
без настройки в BotFather); для соцсетей — t.me/<бот>?startapp=… (нужна «Main Mini App»
в @BotFather: Bot Settings → Configure Mini App).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.bot.ui import Button
from app.config import get_settings
from app.models import Channel
from app.runtime import runtime

TITLES = {Channel.MAX: "MAX", Channel.WEB: "Сайт", Channel.TELEGRAM: "Telegram"}


@dataclass(frozen=True)
class ShareLink:
    channel: Channel
    title: str
    url: str


def _username(channel: Channel) -> str:
    return runtime.bot_usernames.get(channel, "")


def _app_url(query: str) -> str | None:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/app/?{query}" if base.startswith("https://") else None


def share_link(channel: Channel, slug: str) -> str | None:
    """Ссылка для клиентов: отправить в переписке, поставить в профиль соцсетей."""
    u = _username(channel)
    if not u:
        return None
    if channel == Channel.MAX:
        return f"https://max.ru/{u}?startapp={slug}"
    if channel == Channel.TELEGRAM:
        return f"https://t.me/{u}?startapp={slug}"
    return None


def site_link(slug: str) -> str | None:
    """Запись в обычном браузере: okoshko.website/<slug>."""
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/{slug}" if base.startswith("https://") else None


def share_links(slug: str) -> list[ShareLink]:
    """Ссылки во всех подключённых мессенджерах и на сайт (MAX, сайт, Telegram)."""
    out = []
    site = site_link(slug)
    for channel in (Channel.MAX, Channel.WEB, Channel.TELEGRAM):
        if channel == Channel.WEB:
            if site:
                out.append(ShareLink(channel, TITLES[channel], site))
            continue
        if channel in runtime.messengers or _username(channel):
            url = share_link(channel, slug)
            if url:
                out.append(ShareLink(channel, TITLES[channel], url))
    return out


def client_app_button(channel: Channel, text: str, slug: str) -> Button | None:
    """Кнопка в сообщении бота: открыть запись к мастеру."""
    if channel == Channel.TELEGRAM:
        url = _app_url(f"m={slug}")
        if url:
            return Button(text, web_app=url)
    link = share_link(channel, slug)
    return Button(text, url=link) if link else None


def cabinet_button(channel: Channel, text: str) -> Button | None:
    """Кнопка в сообщении бота: открыть кабинет мастера."""
    if channel == Channel.TELEGRAM:
        url = _app_url("cabinet")
        return Button(text, web_app=url) if url else None
    u = _username(channel)
    if channel == Channel.MAX and u:
        return Button(text, url=f"https://max.ru/{u}?startapp=cabinet")
    return None
