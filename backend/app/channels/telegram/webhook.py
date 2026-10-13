"""Приём событий Telegram по вебхуку: POST /telegram/webhook.

Секрет приходит в заголовке X-Telegram-Bot-Api-Secret-Token (задаётся в setWebhook).
Событие обрабатывается фоновой задачей, ответ уходит сразу.
"""
from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from app.bot.handlers import process_event
from app.channels.telegram.adapter import parse_update
from app.config import Settings, get_settings
from app.models import Channel

log = logging.getLogger(__name__)
router = APIRouter(tags=["telegram"])


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict:
    secret = settings.telegram_webhook_secret
    if not secret or not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        raise HTTPException(403, "bad secret")
    try:
        update = await request.json()
    except ValueError:
        return {"ok": True}
    event = parse_update(update) if isinstance(update, dict) else None
    if event is not None:
        background.add_task(process_event, Channel.TELEGRAM, event)
    return {"ok": True}
