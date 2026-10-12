"""Приём событий MAX по вебхуку: POST /max/webhook.

MAX требует ответ 200 за 30 секунд, иначе повторяет доставку — поэтому событие
обрабатывается фоновой задачей, а ответ уходит сразу.
"""
from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from app.bot.handlers import process_event
from app.channels.max.adapter import parse_update
from app.config import Settings, get_settings
from app.models import Channel

log = logging.getLogger(__name__)
router = APIRouter(tags=["max"])


@router.post("/max/webhook")
async def max_webhook(
    request: Request,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    x_max_bot_api_secret: Annotated[str | None, Header()] = None,
) -> dict:
    secret = settings.max_webhook_secret
    if not secret or not hmac.compare_digest(x_max_bot_api_secret or "", secret):
        raise HTTPException(403, "bad secret")
    try:
        update = await request.json()
    except ValueError:
        return {"ok": True}
    log.debug("MAX update: %s", str(update)[:1000])
    event = parse_update(update) if isinstance(update, dict) else None
    if event is not None:
        background.add_task(process_event, Channel.MAX, event)
    return {"ok": True}
