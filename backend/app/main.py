import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.public import router as public_router
from app.channels.max.adapter import MaxMessenger
from app.channels.max.client import MaxClient
from app.channels.max.webhook import router as max_router
from app.config import get_settings
from app.db import SessionLocal
from app.models import Channel
from app.runtime import runtime
from app.workers.reminders import reminder_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("okoshko")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    runtime.session_factory = SessionLocal
    max_client = None
    if settings.max_bot_token:
        max_client = MaxClient(
            settings.max_bot_token, base_url=settings.max_api_base,
            ca_file=settings.max_ca_file or None,
        )
        runtime.messengers[Channel.MAX] = MaxMessenger(max_client)
        username = settings.max_bot_username
        if not username:
            try:
                username = (await max_client.get_me()).get("username") or ""
            except Exception as e:
                log.error("MAX: не удалось получить данные бота (токен? сертификат?): %s", e)
        runtime.bot_usernames[Channel.MAX] = username
        log.info("MAX подключён, бот: %s", username or "?")
    reminders = (
        asyncio.create_task(reminder_loop(settings.reminders_interval_sec))
        if settings.reminders_enabled else None
    )
    try:
        yield
    finally:
        if reminders:
            reminders.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reminders
        if max_client:
            await max_client.close()
        runtime.reset()


app = FastAPI(title="Окошко — запись к мастеру", lifespan=lifespan)
app.include_router(public_router)
app.include_router(max_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
