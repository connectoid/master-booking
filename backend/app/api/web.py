"""Веб-версия: вход из браузера без мессенджера и короткие адреса страниц.

/api/web/... — сессии клиента и мастера; /<slug> и /cabinet — та же Mini App.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser
from app.channels import web_auth
from app.config import Settings, get_settings
from app.domain import qr
from app.models import Channel
from app.runtime import runtime

router = APIRouter(prefix="/api/web", tags=["web"])
pages = APIRouter(tags=["web"])
Conf = Annotated[Settings, Depends(get_settings)]

WEBAPP_DIR = Path(__file__).resolve().parents[3] / "webapp"
WEBAPP_INDEX = WEBAPP_DIR / "index.html"
SITE_DIR = WEBAPP_DIR / "site"
SLUG_RE = re.compile(r"[a-z0-9_]{3,40}")


class TokenOut(BaseModel):
    token: str


class WebConfig(BaseModel):
    max_bot_url: str | None  # бот в MAX — для входа мастера и напоминаний


class RemindLink(BaseModel):
    url: str | None


class MasterLogin(BaseModel):
    code: str = Field(max_length=64)


def max_bot_url() -> str | None:
    u = runtime.bot_usernames.get(Channel.MAX)
    return f"https://max.ru/{u}" if u else None


@router.post("/session", response_model=TokenOut)
async def new_session(settings: Conf) -> TokenOut:
    """Новый анонимный посетитель (браузер хранит токен у себя)."""
    return TokenOut(token=web_auth.new_client_token(settings))


@router.get("/config", response_model=WebConfig)
async def config() -> WebConfig:
    return WebConfig(max_bot_url=max_bot_url())


@router.get("/remind-link", response_model=RemindLink)
async def remind_link(user: CurrentUser, settings: Conf) -> RemindLink:
    """Ссылка на бота MAX: откроет чат, и напоминания о записях из браузера придут туда."""
    if user.channel != Channel.WEB:
        raise HTTPException(400, "Только для записи из браузера")
    bot = max_bot_url()
    if not bot:
        return RemindLink(url=None)
    return RemindLink(url=f"{bot}?start={web_auth.link_code(settings, user.external_user_id)}")


@router.post("/master-login", response_model=TokenOut)
async def master_login(body: MasterLogin, settings: Conf) -> TokenOut:
    master_id = web_auth.verify_login_code(settings, body.code.strip().lower())
    if master_id is None:
        raise HTTPException(401, "Ссылка для входа устарела. Попросите новую в боте: «Кабинет на компьютере».")
    return TokenOut(token=web_auth.master_token(settings, master_id))


@router.post("/admin-login", response_model=TokenOut)
async def admin_login(body: MasterLogin, settings: Conf) -> TokenOut:
    admin_id = web_auth.verify_admin_login_code(settings, body.code.strip().lower())
    if admin_id is None:
        raise HTTPException(401, "Ссылка для входа устарела. Отправьте боту MAX команду /admin ещё раз.")
    return TokenOut(token=web_auth.admin_token(settings, admin_id))


# ---------- страницы ----------

def _index() -> FileResponse:
    return FileResponse(WEBAPP_INDEX, media_type="text/html", headers={"Cache-Control": "no-cache"})


def _site_vars(settings: Settings) -> dict[str, str]:
    e = html.escape
    parts = []
    if settings.site_owner_name:
        parts.append(f"ИП {settings.site_owner_name}")
    if settings.site_inn:
        parts.append(f"ИНН {settings.site_inn}")
    if settings.site_ogrnip:
        parts.append(f"ОГРНИП {settings.site_ogrnip}")
    owner = ", ".join(parts)
    email = settings.site_email
    return {
        "bot_url": e(max_bot_url() or "/cabinet"),
        "owner_block": e(owner),
        "owner_full": e(owner or "владелец сервиса okoshko.website"),
        "email_text": (f'<a href="mailto:{e(email)}">{e(email)}</a>' if email
                       else "сообщение боту «Окошко» в MAX"),
        "email_link": f'<a href="mailto:{e(email)}">{e(email)}</a>' if email else "",
    }


def _site_page(name: str, settings: Settings) -> HTMLResponse:
    text = (SITE_DIR / name).read_text(encoding="utf-8")
    for key, value in _site_vars(settings).items():
        text = text.replace("{{" + key + "}}", value)
    return HTMLResponse(text, headers={"Cache-Control": "no-cache"})


@pages.get("/", include_in_schema=False)
async def home(settings: Conf) -> HTMLResponse:
    return _site_page("index.html", settings)


@pages.get("/start", include_in_schema=False)
async def start_page(settings: Conf) -> HTMLResponse:
    """«Создать кабинет»: шаги регистрации и QR-код на бота MAX."""
    return _site_page("start.html", settings)


_qr_cache: dict[str, str] = {}


@pages.get("/qr/max-bot.svg", include_in_schema=False)
async def max_bot_qr() -> Response:
    url = max_bot_url()
    if not url:
        raise HTTPException(404, "Бот MAX не подключён")
    if url not in _qr_cache:
        _qr_cache[url] = qr.svg(url)
    return Response(_qr_cache[url], media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@pages.get("/privacy", include_in_schema=False)
async def privacy(settings: Conf) -> HTMLResponse:
    return _site_page("privacy.html", settings)


@pages.get("/cabinet", include_in_schema=False)
async def cabinet_page() -> FileResponse:
    return _index()


@pages.get("/admin", include_in_schema=False)
async def admin_page() -> FileResponse:
    return _index()


@pages.get("/{slug}", include_in_schema=False)
async def master_page(slug: str) -> FileResponse:
    """okoshko.website/<slug> — запись к мастеру в браузере."""
    if not SLUG_RE.fullmatch(slug):
        raise HTTPException(404, "Not Found")
    return _index()
