"""Веб-версия: вход из браузера без мессенджера и короткие адреса страниц.

/api/web/... — сессии клиента и мастера; /<slug> и /cabinet — та же Mini App.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser
from app.channels import web_auth
from app.config import Settings, get_settings
from app.models import Channel
from app.runtime import runtime

router = APIRouter(prefix="/api/web", tags=["web"])
pages = APIRouter(tags=["web"])
Conf = Annotated[Settings, Depends(get_settings)]

WEBAPP_INDEX = Path(__file__).resolve().parents[3] / "webapp" / "index.html"
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


# ---------- страницы ----------

def _index() -> FileResponse:
    return FileResponse(WEBAPP_INDEX, media_type="text/html", headers={"Cache-Control": "no-cache"})


@pages.get("/cabinet", include_in_schema=False)
async def cabinet_page() -> FileResponse:
    return _index()


@pages.get("/{slug}", include_in_schema=False)
async def master_page(slug: str) -> FileResponse:
    """okoshko.website/<slug> — запись к мастеру в браузере."""
    if not SLUG_RE.fullmatch(slug):
        raise HTTPException(404, "Not Found")
    return _index()
