import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.cabinet import router as cabinet_router
from app.api.public import router as public_router
from app.channels.max.adapter import MaxMessenger
from app.channels.max.client import MaxClient
from app.channels.max.webhook import router as max_router
from app.channels.telegram.adapter import TelegramMessenger
from app.channels.telegram.client import TelegramClient
from app.channels.telegram.webhook import router as telegram_router
from app.config import get_settings
from app.db import SessionLocal
from app.models import Channel
from app.runtime import runtime
from app.workers.reminders import reminder_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx пишет в INFO полный адрес запроса, а у Telegram в нём токен бота
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("okoshko")


async def _start_telegram(settings) -> TelegramClient | None:
    if not settings.telegram_bot_token:
        return None
    client = TelegramClient(settings.telegram_bot_token, base_url=settings.telegram_api_base)
    runtime.messengers[Channel.TELEGRAM] = TelegramMessenger(client)
    username = settings.telegram_bot_username.lstrip("@")
    if not username:
        try:
            username = (await client.get_me()).get("username") or ""
        except Exception as e:
            log.error("Telegram: не удалось получить данные бота (токен? доступ к API?): %s", e)
    runtime.bot_usernames[Channel.TELEGRAM] = username
    log.info("Telegram подключён, бот: %s", username or "?")
    return client


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    runtime.session_factory = SessionLocal
    max_client = None
    if settings.max_bot_token:
        try:
            max_client = MaxClient(
                settings.max_bot_token, base_url=settings.max_api_base,
                ca_file=settings.max_ca_file or None,
            )
        except Exception as e:  # битый файл сертификата и т.п. — API продолжает работать
            log.error("MAX отключён: не удалось создать клиент (MAX_CA_FILE?): %s", e)
    if max_client is not None:
        runtime.messengers[Channel.MAX] = MaxMessenger(max_client)
        username = settings.max_bot_username
        if not username:
            try:
                username = (await max_client.get_me()).get("username") or ""
            except Exception as e:
                log.error("MAX: не удалось получить данные бота (токен? сертификат?): %s", e)
        runtime.bot_usernames[Channel.MAX] = username
        log.info("MAX подключён, бот: %s", username or "?")
    tg_client = await _start_telegram(settings)
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
        if tg_client:
            await tg_client.close()
        runtime.reset()


app = FastAPI(title="Окошко — запись к мастеру", lifespan=lifespan)
app.include_router(public_router)
app.include_router(cabinet_router)
app.include_router(max_router)
app.include_router(telegram_router)


WEBAPP_DIR = Path(__file__).resolve().parents[2] / "webapp"
if WEBAPP_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
